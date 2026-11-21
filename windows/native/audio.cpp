#include "capture.h"
#include <audioclient.h>
#include <audiopolicy.h>
#include <cmath>
#include <cstring>
#include <ks.h>
#include <ksmedia.h>
#include <memory>
#include <mmdeviceapi.h>
#include <mmreg.h>
#include <set>
#include <winternl.h>

// Defined by the Windows SDK audioclientactivationparams.h; MinGW also supports the ABI
// via mmdeviceapi.h but some distributions do not ship the activation-parameters header.
#if __has_include(<audioclientactivationparams.h>)
#include <audioclientactivationparams.h>
#endif
#ifndef VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK
enum AUDIOCLIENT_ACTIVATION_TYPE {
    AUDIOCLIENT_ACTIVATION_TYPE_DEFAULT,
    AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK
};
enum PROCESS_LOOPBACK_MODE {
    PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE,
    PROCESS_LOOPBACK_MODE_EXCLUDE_TARGET_PROCESS_TREE
};
struct AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS {
    DWORD TargetProcessId;
    PROCESS_LOOPBACK_MODE ProcessLoopbackMode;
};
struct AUDIOCLIENT_ACTIVATION_PARAMS {
    AUDIOCLIENT_ACTIVATION_TYPE ActivationType;
    union {
        AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS ProcessLoopbackParams;
    };
};
#define VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK L"VAD\\Process_Loopback"
#endif

static bool process_loopback_supported() {
    auto version = reinterpret_cast<NTSTATUS(WINAPI *)(RTL_OSVERSIONINFOW *)>(
        GetProcAddress(GetModuleHandleW(L"ntdll.dll"), "RtlGetVersion"));
    RTL_OSVERSIONINFOW v{};
    v.dwOSVersionInfoSize = sizeof(v);
    return version && version(&v) == 0 && v.dwBuildNumber >= 20348;
}

static ComPtr<IMMDeviceEnumerator> devices() {
    ComPtr<IMMDeviceEnumerator> enumerator;
    check(CoCreateInstance(__uuidof(MMDeviceEnumerator), nullptr, CLSCTX_ALL, IID_PPV_ARGS(&enumerator)),
          "Open Windows audio devices");
    return enumerator;
}

static void list_sessions(IMMDevice *device, std::set<DWORD> &seen) {
    ComPtr<IAudioSessionManager2> manager;
    if (FAILED(device->Activate(__uuidof(IAudioSessionManager2), CLSCTX_ALL, nullptr,
                                reinterpret_cast<void **>(manager.GetAddressOf()))))
        return;
    ComPtr<IAudioSessionEnumerator> sessions;
    if (FAILED(manager->GetSessionEnumerator(&sessions)))
        return;
    int count = 0;
    if (FAILED(sessions->GetCount(&count)))
        return;
    for (int i = 0; i < count; i++) {
        ComPtr<IAudioSessionControl> control;
        ComPtr<IAudioSessionControl2> session;
        AudioSessionState state;
        DWORD pid = 0;
        if (FAILED(sessions->GetSession(i, &control)) || FAILED(control.As(&session)) ||
            FAILED(session->GetState(&state)) || state != AudioSessionStateActive ||
            FAILED(session->GetProcessId(&pid)) || !pid || !seen.insert(pid).second)
            continue;
        auto id = process_token(pid);
        if (!id.empty())
            record_json("win-app", id, process_name(pid) + L" (PID " + std::to_wstring(pid) + L")");
    }
}

void audio_sources() {
    auto enumerator = devices();
    std::set<DWORD> seen;
    const bool by_process = process_loopback_supported();
    const PROPERTYKEY friendly_name = {
        {0xa45c254e, 0xdf1c, 0x4efd, {0x80, 0x20, 0x67, 0xd1, 0x46, 0xa8, 0x50, 0xe0}}, 14};
    for (EDataFlow flow : {eCapture, eRender}) {
        ComPtr<IMMDeviceCollection> endpoints;
        check(enumerator->EnumAudioEndpoints(flow, DEVICE_STATE_ACTIVE, &endpoints),
              "Enumerate Windows audio endpoints");
        UINT count = 0;
        check(endpoints->GetCount(&count), "Count Windows audio endpoints");
        for (UINT i = 0; i < count; i++) {
            ComPtr<IMMDevice> device;
            LPWSTR raw_id = nullptr;
            if (FAILED(endpoints->Item(i, &device)) || FAILED(device->GetId(&raw_id)))
                continue;
            std::wstring id(raw_id);
            CoTaskMemFree(raw_id);
            std::wstring label = id;
            ComPtr<IPropertyStore> props;
            PROPVARIANT value{};
            if (SUCCEEDED(device->OpenPropertyStore(STGM_READ, &props)) &&
                SUCCEEDED(props->GetValue(friendly_name, &value)) && value.vt == VT_LPWSTR && value.pwszVal)
                label = value.pwszVal;
            PropVariantClear(&value);
            record_json(flow == eCapture ? "win-mic" : "win-monitor", id, label);
            if (by_process && flow == eRender)
                list_sessions(device.Get(), seen);
        }
    }
}

class Activation final : public IActivateAudioInterfaceCompletionHandler {
    LONG references = 1;
    ComPtr<IUnknown> marshaler;

  public:
    Handle complete{CreateEventW(nullptr, TRUE, FALSE, nullptr)};
    HRESULT result = E_UNEXPECTED;
    ComPtr<IAudioClient> client;
    Activation() {
        if (!complete.value)
            throw std::runtime_error("Cannot create activation event");
        check(CoCreateFreeThreadedMarshaler(this, &marshaler), "Create audio activation marshaler");
    }
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void **out) override {
        if (!out)
            return E_POINTER;
        if (iid == IID_IUnknown || iid == __uuidof(IActivateAudioInterfaceCompletionHandler) ||
            iid == IID_IAgileObject) {
            *out = static_cast<IActivateAudioInterfaceCompletionHandler *>(this);
            AddRef();
            return S_OK;
        }
        if (iid == IID_IMarshal)
            return marshaler->QueryInterface(iid, out);
        *out = nullptr;
        return E_NOINTERFACE;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return InterlockedIncrement(&references); }
    ULONG STDMETHODCALLTYPE Release() override {
        ULONG n = InterlockedDecrement(&references);
        if (!n)
            delete this;
        return n;
    }
    HRESULT STDMETHODCALLTYPE ActivateCompleted(IActivateAudioInterfaceAsyncOperation *request) override {
        HRESULT activated = E_UNEXPECTED;
        ComPtr<IUnknown> iface;
        result = request->GetActivateResult(&activated, &iface);
        if (SUCCEEDED(result))
            result = activated;
        if (SUCCEEDED(result))
            result = iface.As(&client);
        SetEvent(complete.value);
        return S_OK;
    }
};

static ComPtr<IAudioClient> activate_process(DWORD pid) {
    if (!process_loopback_supported())
        throw std::runtime_error("Per-application capture requires Windows build 20348 or later.");
    ComPtr<Activation> handler;
    handler.Attach(new Activation());
    AUDIOCLIENT_ACTIVATION_PARAMS params{};
    params.ActivationType = AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK;
    params.ProcessLoopbackParams.TargetProcessId = pid;
    params.ProcessLoopbackParams.ProcessLoopbackMode = PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE;
    PROPVARIANT activation{};
    activation.vt = VT_BLOB;
    activation.blob.cbSize = sizeof(params);
    activation.blob.pBlobData = reinterpret_cast<BYTE *>(&params);
    ComPtr<IActivateAudioInterfaceAsyncOperation> request;
    check(ActivateAudioInterfaceAsync(VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK, __uuidof(IAudioClient),
                                      &activation, handler.Get(), &request),
          "Activate process loopback");
    if (WaitForSingleObject(handler->complete.value, 10000) != WAIT_OBJECT_0)
        throw std::runtime_error("Process loopback activation timed out");
    check(handler->result, "Open selected process loopback");
    return handler->client;
}

// Downmix the actual shared-mode endpoint format. Accumulated fractional sample
// periods are kept between WASAPI packets, including at 44.1 kHz stereo.
class PcmConverter {
    const WAVEFORMATEX &format;
    bool floating;
    uint32_t filled = 0;
    double sum = 0;
    double value(const BYTE *p) const {
        if (floating) {
            if (format.wBitsPerSample == 64) {
                double x;
                std::memcpy(&x, p, 8);
                return x;
            }
            float x;
            std::memcpy(&x, p, 4);
            return x;
        }
        if (format.wBitsPerSample == 8)
            return (p[0] - 128) / 128.0;
        if (format.wBitsPerSample == 16) {
            int16_t x;
            std::memcpy(&x, p, 2);
            return x / 32768.0;
        }
        if (format.wBitsPerSample == 24) {
            auto x = int32_t(p[0]) | (int32_t(p[1]) << 8) | (int32_t(p[2]) << 16);
            if (x & 0x800000)
                x -= 0x1000000;
            return x / 8388608.0;
        }
        int32_t x;
        std::memcpy(&x, p, 4);
        return x / 2147483648.0;
    }

  public:
    explicit PcmConverter(const WAVEFORMATEX &fmt) : format(fmt) {
        auto type = fmt.wFormatTag;
        if (type == WAVE_FORMAT_EXTENSIBLE && fmt.cbSize >= 22) {
            auto &subtype = reinterpret_cast<const WAVEFORMATEXTENSIBLE &>(fmt).SubFormat;
            if (subtype == KSDATAFORMAT_SUBTYPE_PCM)
                type = WAVE_FORMAT_PCM;
            else if (subtype == KSDATAFORMAT_SUBTYPE_IEEE_FLOAT)
                type = WAVE_FORMAT_IEEE_FLOAT;
        }
        floating = type == WAVE_FORMAT_IEEE_FLOAT;
        const unsigned bits = fmt.wBitsPerSample;
        if ((!floating && type != WAVE_FORMAT_PCM) || (floating && bits != 32 && bits != 64) ||
            (!floating && bits != 8 && bits != 16 && bits != 24 && bits != 32) || !fmt.nChannels ||
            !fmt.nSamplesPerSec || fmt.nBlockAlign != fmt.nChannels * (bits / 8))
            throw std::runtime_error("Unsupported audio endpoint PCM format");
    }
    std::vector<int16_t> packet(const BYTE *data, UINT32 frames, bool silent) {
        std::vector<int16_t> samples;
        samples.reserve((uint64_t(frames) * 16000 + filled) / format.nSamplesPerSec + 1);
        for (UINT32 i = 0; i < frames; ++i) {
            double mixed = 0;
            if (!silent) {
                const auto *row = data + i * format.nBlockAlign;
                for (unsigned channel = 0; channel < format.nChannels; channel++)
                    mixed += value(row + channel * (format.wBitsPerSample / 8));
                mixed /= format.nChannels;
                if (!std::isfinite(mixed))
                    mixed = 0;
            }
            uint32_t remaining = 16000;
            while (remaining) {
                auto take = std::min<uint32_t>(remaining, format.nSamplesPerSec - filled);
                sum += mixed * take;
                remaining -= take;
                filled += take;
                if (filled == format.nSamplesPerSec) {
                    auto level = std::clamp(sum / filled, -1.0, 1.0) * 32768.0;
                    samples.push_back(
                        static_cast<int16_t>(std::clamp(std::round(level), -32768.0, 32767.0)));
                    sum = 0;
                    filled = 0;
                }
            }
        }
        return samples;
    }
};

void capture_audio(const std::wstring &kind, const std::wstring &id) {
    const bool app = kind == L"win-app";
    ComPtr<IAudioClient> client;
    DWORD pid = 0;
    Handle process(nullptr);
    WAVEFORMATEX requested{WAVE_FORMAT_PCM, 2, 44100, 176400, 4, 16, 0};
    WAVEFORMATEX *format = &requested;
    std::unique_ptr<WAVEFORMATEX, decltype(&CoTaskMemFree)> mix(nullptr, &CoTaskMemFree);
    DWORD flags = AUDCLNT_STREAMFLAGS_EVENTCALLBACK;
    if (app) {
        pid = std::stoul(id);
        // Pin the original process object before checking its birth time to guard PID reuse.
        process.value = OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid);
        if (!process.value || process_token(pid) != id)
            throw std::runtime_error("Selected application has closed. Refresh audio sources.");
        client = activate_process(pid);
        flags |= AUDCLNT_STREAMFLAGS_LOOPBACK | AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM;
    } else {
        if (kind != L"win-monitor" && kind != L"win-mic")
            throw std::runtime_error("Unknown audio source");
        ComPtr<IMMDevice> device;
        check(devices()->GetDevice(id.c_str(), &device), "Open selected device");
        check(device->Activate(__uuidof(IAudioClient), CLSCTX_ALL, nullptr,
                               reinterpret_cast<void **>(client.GetAddressOf())),
              "Activate audio device");
        check(client->GetMixFormat(&format), "Read endpoint sample format");
        mix.reset(format);
        if (kind == L"win-monitor")
            flags |= AUDCLNT_STREAMFLAGS_LOOPBACK;
    }
    PcmConverter converter(*format);
    check(client->Initialize(AUDCLNT_SHAREMODE_SHARED, flags, 0, 0, format, nullptr),
          "Initialize selected audio source");
    Handle ready(CreateEventW(nullptr, FALSE, FALSE, nullptr));
    if (!ready.value)
        throw std::runtime_error("Cannot create audio event");
    check(client->SetEventHandle(ready.value), "Set audio event");
    ComPtr<IAudioCaptureClient> capture;
    check(client->GetService(IID_PPV_ARGS(&capture)), "Get capture service");
    check(client->Start(), "Start WASAPI capture");
    while (true) {
        if (process.value && WaitForSingleObject(process.value, 0) == WAIT_OBJECT_0)
            throw std::runtime_error("Selected application exited");
        // Render endpoints need not signal during silence. Periodic polling also reports lost devices.
        WaitForSingleObject(ready.value, 200);
        UINT32 packet = 0;
        check(capture->GetNextPacketSize(&packet), "Read WASAPI packet");
        while (packet) {
            BYTE *data = nullptr;
            UINT32 frames = 0;
            DWORD status = 0;
            check(capture->GetBuffer(&data, &frames, &status, nullptr, nullptr), "Acquire WASAPI buffer");
            auto pcm = converter.packet(data, frames, (status & AUDCLNT_BUFFERFLAGS_SILENT) != 0);
            check(capture->ReleaseBuffer(frames), "Release WASAPI buffer");
            write_output(pcm.data(), pcm.size() * sizeof(int16_t));
            check(capture->GetNextPacketSize(&packet), "Read WASAPI packet");
        }
    }
}

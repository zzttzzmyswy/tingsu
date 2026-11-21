// Hardware-path acceptance tests for the same native helper that ships in Tingsu.
// A renderer and a window fixture run as independent copies of this executable.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>

#include <algorithm>
#include <cmath>
#include <complex>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <memory>
#include <mmsystem.h>
#include <stdexcept>
#include <string>
#include <vector>

namespace fs = std::filesystem;

static void require(bool condition, const char *message) {
    if (!condition)
        throw std::runtime_error(message);
}

class Scratch {
  public:
    fs::path path;
    Scratch() {
        wchar_t dir[MAX_PATH], file[MAX_PATH];
        require(GetTempPathW(MAX_PATH, dir) && GetTempFileNameW(dir, L"TSQ", 0, file),
                "Cannot create test workspace");
        DeleteFileW(file);
        require(CreateDirectoryW(file, nullptr), "Cannot create test directory");
        path = file;
    }
    ~Scratch() {
        std::error_code ignored;
        fs::remove_all(path, ignored);
    }
};

class Child {
    PROCESS_INFORMATION proc{};

  public:
    fs::path output;
    Child(const fs::path &executable, const std::wstring &args, fs::path out) : output(std::move(out)) {
        SECURITY_ATTRIBUTES sa{sizeof(sa), nullptr, TRUE};
        HANDLE file =
            CreateFileW(output.c_str(), GENERIC_WRITE, FILE_SHARE_READ, &sa, CREATE_ALWAYS, 0, nullptr);
        HANDLE errors = CreateFileW((output.wstring() + L".err").c_str(), GENERIC_WRITE, FILE_SHARE_READ,
                                    &sa, CREATE_ALWAYS, 0, nullptr);
        HANDLE input = CreateFileW(L"NUL", GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, &sa,
                                   OPEN_EXISTING, 0, nullptr);
        STARTUPINFOW si{};
        si.cb = sizeof(si);
        si.dwFlags = STARTF_USESTDHANDLES;
        si.hStdOutput = file;
        si.hStdError = errors;
        si.hStdInput = input;
        std::wstring command = L"\"" + executable.wstring() + L"\" " + args;
        bool started = file != INVALID_HANDLE_VALUE && errors != INVALID_HANDLE_VALUE &&
                       input != INVALID_HANDLE_VALUE &&
                       CreateProcessW(executable.c_str(), command.data(), nullptr, nullptr, TRUE,
                                      CREATE_NO_WINDOW, nullptr, nullptr, &si, &proc);
        for (auto handle : {file, errors, input})
            if (handle != INVALID_HANDLE_VALUE)
                CloseHandle(handle);
        require(started, "Cannot start test subprocess");
    }
    DWORD pid() const { return proc.dwProcessId; }
    DWORD wait(DWORD ms) const {
        require(WaitForSingleObject(proc.hProcess, ms) == WAIT_OBJECT_0, "Test subprocess timed out");
        DWORD code = 0;
        GetExitCodeProcess(proc.hProcess, &code);
        return code;
    }
    void stop() {
        if (WaitForSingleObject(proc.hProcess, 0) == WAIT_TIMEOUT) {
            TerminateProcess(proc.hProcess, 1);
            WaitForSingleObject(proc.hProcess, 3000);
        }
    }
    Child(const Child &) = delete;
    ~Child() {
        stop();
        CloseHandle(proc.hThread);
        CloseHandle(proc.hProcess);
    }
};

static std::string read(const fs::path &file) {
    std::ifstream stream(file, std::ios::binary);
    require(stream.good(), "Cannot read test output");
    return {std::istreambuf_iterator<char>(stream), std::istreambuf_iterator<char>()};
}

static std::string run(const fs::path &helper, const std::wstring &args, const fs::path &path) {
    Child p(helper, args, path);
    if (p.wait(10000) != 0)
        throw std::runtime_error("Capture helper: " + read(path.wstring() + L".err"));
    return read(path);
}

static std::wstring ascii_wide(const std::string &s) { return {s.begin(), s.end()}; }

// Native IDs are ASCII. Labels may contain arbitrary Unicode and are intentionally ignored.
static std::string id_for(const std::string &records, const std::string &kind,
                          const std::string &prefix = {}) {
    auto start = records.find("\"kind\":\"" + kind + "\",\"id\":\"" + prefix);
    if (start == std::string::npos)
        return {};
    start = records.find("\"id\":\"", start) + 6;
    return records.substr(start, records.find('"', start) - start);
}

static int tone(double frequency) {
    WAVEFORMATEX fmt{WAVE_FORMAT_PCM, 2, 48000, 192000, 4, 16, 0};
    HWAVEOUT out;
    require(waveOutOpen(&out, WAVE_MAPPER, &fmt, 0, 0, CALLBACK_NULL) == MMSYSERR_NOERROR,
            "Cannot open test playback device");
    std::vector<int16_t> samples(48000 * 2);
    for (int i = 0; i < 48000; i++) {
        auto level = static_cast<int16_t>(std::sin(2 * 3.141592653589793 * frequency * i / 48000) * 4200);
        samples[2 * i] = samples[2 * i + 1] = level;
    }
    WAVEHDR header{};
    header.lpData = reinterpret_cast<char *>(samples.data());
    header.dwBufferLength = static_cast<DWORD>(samples.size() * 2);
    header.dwLoops = 60;
    header.dwFlags = WHDR_BEGINLOOP | WHDR_ENDLOOP;
    require(waveOutPrepareHeader(out, &header, sizeof(header)) == MMSYSERR_NOERROR,
            "Cannot prepare test tone");
    require(waveOutWrite(out, &header, sizeof(header)) == MMSYSERR_NOERROR, "Cannot play test tone");
    Sleep(60000);
    waveOutReset(out);
    waveOutUnprepareHeader(out, &header, sizeof(header));
    waveOutClose(out);
    return 0;
}

static double strength(const std::vector<int16_t> &samples, double frequency) {
    std::complex<double> sum{};
    for (size_t i = 0; i < samples.size(); ++i)
        sum += double(samples[i]) * std::polar(1.0, 2 * 3.141592653589793 * frequency * i / 16000);
    return samples.empty() ? 0 : 2 * std::abs(sum) / samples.size();
}

static void audio_test(const fs::path &self, const fs::path &helper, const fs::path &temp) {
    Child a(self, L"--tone 749", temp / L"rendererA");
    Child b(self, L"--tone 1439", temp / L"rendererB");
    std::string ida, idb, mix;
    for (int i = 0; i < 8 && (ida.empty() || idb.empty()); ++i) {
        Sleep(450);
        auto rows = run(helper, L"audio-sources", temp / L"devices");
        ida = id_for(rows, "win-app", std::to_string(a.pid()) + "/");
        idb = id_for(rows, "win-app", std::to_string(b.pid()) + "/");
        // Choose the endpoint on which the two WinMM renderers actually play.
        auto marker = rows.find("\"kind\":\"win-app\",\"id\":\"" + ida + "\"");
        if (!ida.empty() && marker != std::string::npos) {
            auto endpt = rows.rfind("\"kind\":\"win-monitor\"", marker);
            if (endpt != std::string::npos)
                mix = id_for(rows.substr(endpt), "win-monitor");
        }
    }
    require(!ida.empty() && !idb.empty() && !mix.empty(),
            "Missing test streams. Use Windows build 20348+ with an enabled default playback device.");
    Child ca(helper, L"audio win-app " + ascii_wide(ida), temp / L"first.pcm");
    Child cb(helper, L"audio win-app " + ascii_wide(idb), temp / L"second.pcm");
    Child cs(helper, L"audio win-monitor " + ascii_wide(mix), temp / L"system.pcm");
    Sleep(6500);
    for (auto child : {&ca, &cb, &cs})
        child->stop();
    a.stop();
    b.stop();
    for (auto child : {&ca, &cb, &cs}) {
        auto raw = read(child->output);
        if (raw.size() < 64000)
            throw std::runtime_error("Missing 16 kHz audio: " + read(child->output.wstring() + L".err"));
        std::vector<int16_t> pcm(raw.size() / 2);
        std::memcpy(pcm.data(), raw.data(), pcm.size() * 2);
        double x = strength(pcm, 749), y = strength(pcm, 1439);
        std::cout << child->output.filename().string() << " samples=" << pcm.size() << " 749Hz=" << x
                  << " 1439Hz=" << y << std::endl;
        if (child == &ca)
            require(x > 100 && x > 12 * y, "First process contains other application's tone");
        if (child == &cb)
            require(y > 100 && y > 12 * x, "Second process contains other application's tone");
        if (child == &cs)
            require(x > 100 && y > 100, "System loopback did not contain both tones");
    }
    std::cout << "Audio endpoint, process discovery and isolation PASS" << std::endl;
}

const COLORREF first_color = RGB(191, 74, 39), second_color = RGB(30, 50, 220),
               cover_color = RGB(36, 190, 58);

static LRESULT CALLBACK test_window_proc(HWND window, UINT msg, WPARAM w, LPARAM l) {
    if (msg == WM_PAINT || msg == WM_PRINT || msg == WM_PRINTCLIENT) {
        PAINTSTRUCT ps{};
        HDC target = msg == WM_PAINT ? BeginPaint(window, &ps) : reinterpret_cast<HDC>(w);
        RECT bounds{};
        GetClientRect(window, &bounds);
        auto color = static_cast<COLORREF>(GetWindowLongPtrW(window, GWLP_USERDATA));
        HBRUSH brush = CreateSolidBrush(color);
        FillRect(target, &bounds, brush);
        DeleteObject(brush);
        if (msg == WM_PAINT)
            EndPaint(window, &ps);
        return 0;
    }
    if (msg == WM_APP + 1) {
        SetWindowLongPtrW(window, GWLP_USERDATA, second_color);
        InvalidateRect(window, nullptr, TRUE);
        UpdateWindow(window);
        return 0;
    }
    if (msg == WM_DESTROY) {
        PostQuitMessage(0);
        return 0;
    }
    return DefWindowProcW(window, msg, w, l);
}

static int window_fixture(bool cover) {
    auto instance = GetModuleHandleW(nullptr);
    WNDCLASSW cls{};
    cls.lpfnWndProc = test_window_proc;
    cls.hInstance = instance;
    cls.lpszClassName = L"TingsuAcceptanceFixture";
    require(RegisterClassW(&cls) != 0, "Cannot register test window");
    HWND window = CreateWindowExW(cover ? WS_EX_TOPMOST : 0, cls.lpszClassName,
                                  cover ? L"Tingsu QA Cover" : L"Tingsu QA Target", WS_POPUP, 130, 125, 404,
                                  222, nullptr, nullptr, instance, nullptr);
    require(window != nullptr, "Cannot create test window");
    SetWindowLongPtrW(window, GWLP_USERDATA, cover ? cover_color : first_color);
    ShowWindow(window, SW_SHOW);
    UpdateWindow(window);
    MSG m;
    while (GetMessageW(&m, nullptr, 0, 0) > 0) {
        TranslateMessage(&m);
        DispatchMessageW(&m);
    }
    return 0;
}

static HWND find_fixture(const wchar_t *title, DWORD expected_pid) {
    for (int i = 0; i < 50; ++i) {
        HWND w = FindWindowW(L"TingsuAcceptanceFixture", title);
        DWORD pid = 0;
        GetWindowThreadProcessId(w, &pid);
        if (w && pid == expected_pid)
            return w;
        Sleep(100);
    }
    throw std::runtime_error("Test window did not appear");
}

static void check_bitmap(const std::string &raw, COLORREF expected) {
    BITMAPFILEHEADER file{};
    BITMAPINFOHEADER info{};
    require(raw.size() >= sizeof(file) + sizeof(info), "Missing BMP header");
    std::memcpy(&file, raw.data(), sizeof(file));
    std::memcpy(&info, raw.data() + sizeof(file), sizeof(info));
    require(file.bfType == 0x4d42 && info.biWidth == 404 && std::abs(info.biHeight) == 222 &&
                info.biBitCount == 32,
            "Image does not match selected window dimensions");
    size_t center = file.bfOffBits + (111 * 404 + 202) * 4;
    require(center + 3 <= raw.size(), "Incomplete bitmap");
    const auto *p = reinterpret_cast<const unsigned char *>(raw.data() + center);
    std::cout << "Window 404x222 center RGB " << int(p[2]) << "," << int(p[1]) << "," << int(p[0])
              << std::endl;
    require(std::abs(int(p[2]) - GetRValue(expected)) < 5 &&
                std::abs(int(p[1]) - GetGValue(expected)) < 5 &&
                std::abs(int(p[0]) - GetBValue(expected)) < 5,
            "Selected window pixels do not match fixture");
}

static void window_test(const fs::path &self, const fs::path &helper, const fs::path &temp) {
    Child target(self, L"--target", temp / L"target");
    HWND hwnd = find_fixture(L"Tingsu QA Target", target.pid());
    auto rows = run(helper, L"windows", temp / L"windows");
    auto token = id_for(rows, "window",
                        std::to_string(reinterpret_cast<uintptr_t>(hwnd)) + "/" +
                            std::to_string(target.pid()) + "/");
    require(!token.empty(), "Target window missing from selector");
    Child cover(self, L"--cover", temp / L"cover");
    find_fixture(L"Tingsu QA Cover", cover.pid());
    const auto command = L"frame " + ascii_wide(token);
    check_bitmap(run(helper, command, temp / L"first.bmp"), first_color);
    SendMessageW(hwnd, WM_APP + 1, 0, 0);
    check_bitmap(run(helper, command, temp / L"second.bmp"), second_color);
    target.stop();
    Child stale(helper, command, temp / L"stale.bmp");
    require(stale.wait(7000) != 0 && read(stale.output).empty(),
            "Stale window token unexpectedly returned an image");
    std::cout << "Window isolation, update and stale handle PASS" << std::endl;
}

int wmain(int argc, wchar_t **argv) {
    SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
    try {
        if (argc == 3 && std::wstring(argv[1]) == L"--tone")
            return tone(std::stod(argv[2]));
        if (argc == 2 && std::wstring(argv[1]) == L"--target")
            return window_fixture(false);
        if (argc == 2 && std::wstring(argv[1]) == L"--cover")
            return window_fixture(true);
        require(argc == 2 || argc == 3,
                "Usage: tingsu-native-qa.exe PATH-TO-HELPER [--audio-only | --window-only]");
        std::wstring mode = argc == 3 ? argv[2] : L"";
        require(mode.empty() || mode == L"--audio-only" || mode == L"--window-only", "Unknown test mode");
        wchar_t self[32768];
        GetModuleFileNameW(nullptr, self, 32768);
        auto helper = fs::absolute(argv[1]);
        require(fs::exists(helper), "Capture helper was not found");
        Scratch scratch;
        if (mode != L"--window-only")
            audio_test(self, helper, scratch.path);
        if (mode != L"--audio-only")
            window_test(self, helper, scratch.path);
        return 0;
    } catch (const std::exception &e) {
        std::cerr << "FAIL: " << e.what() << std::endl;
        return 1;
    }
}

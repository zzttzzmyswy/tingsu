#include "capture.h"
#include <roapi.h>

std::wstring process_token(DWORD pid) {
    Handle process(OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid));
    FILETIME creation{}, exit{}, system{}, user{};
    if (!process.value || !GetProcessTimes(process.value, &creation, &exit, &system, &user))
        return {};
    ULARGE_INTEGER start;
    start.LowPart = creation.dwLowDateTime;
    start.HighPart = creation.dwHighDateTime;
    return std::to_wstring(pid) + L"/" + std::to_wstring(start.QuadPart);
}

std::wstring process_name(DWORD pid) {
    Handle process(OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid));
    wchar_t path[32768];
    DWORD size = static_cast<DWORD>(std::size(path));
    if (!process.value || !QueryFullProcessImageNameW(process.value, 0, path, &size))
        return L"";
    std::wstring name(path, size);
    auto slash = name.find_last_of(L"\\/");
    return slash == std::wstring::npos ? name : name.substr(slash + 1);
}

static std::string json_string(const std::wstring &s) {
    int bytes =
        WideCharToMultiByte(CP_UTF8, 0, s.data(), static_cast<int>(s.size()), nullptr, 0, nullptr, nullptr);
    std::string data(bytes, 0);
    WideCharToMultiByte(CP_UTF8, 0, s.data(), static_cast<int>(s.size()), data.data(), bytes, nullptr,
                        nullptr);
    std::string out = "\"";
    for (unsigned char c : data) {
        if (c == '"' || c == '\\')
            out.push_back('\\');
        if (c < 32) {
            char escaped[7];
            std::snprintf(escaped, sizeof(escaped), "\\u%04x", c);
            out += escaped;
        } else
            out.push_back(static_cast<char>(c));
    }
    return out + "\"";
}

void record_json(const std::string &kind, const std::wstring &id, const std::wstring &label) {
    auto row =
        "{\"kind\":\"" + kind + "\",\"id\":" + json_string(id) + ",\"label\":" + json_string(label) + "}\n";
    write_output(row.data(), row.size());
}

void write_output(const void *data, size_t size) {
    // Redirected stdout may be a pipe owned by the desktop app. Respond promptly to a closed reader.
    const BYTE *p = static_cast<const BYTE *>(data);
    while (size) {
        DWORD sent = 0;
        if (!WriteFile(GetStdHandle(STD_OUTPUT_HANDLE), p,
                       static_cast<DWORD>(std::min<size_t>(size, 65536)), &sent, nullptr) ||
            sent == 0)
            throw std::runtime_error("Output pipe closed");
        size -= sent;
        p += sent;
    }
}

int wmain(int argc, wchar_t **argv) {
    SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
    const HRESULT init = RoInitialize(RO_INIT_MULTITHREADED);
    try {
        check(init, "Initialize Windows Runtime");
        if (argc == 2 && std::wstring(argv[1]) == L"audio-sources")
            audio_sources();
        else if (argc == 4 && std::wstring(argv[1]) == L"audio")
            capture_audio(argv[2], argv[3]);
        else if (argc == 2 && std::wstring(argv[1]) == L"windows")
            list_windows();
        else if (argc == 3 && std::wstring(argv[1]) == L"frame")
            capture_window(argv[2]);
        else
            throw std::runtime_error(
                "Usage: tingsu-capture audio-sources | audio KIND ID | windows | frame WINDOW-ID");
    } catch (const std::exception &e) {
        std::fprintf(stderr, "%s\n", e.what());
        if (SUCCEEDED(init))
            RoUninitialize();
        return 1;
    }
    RoUninitialize();
    return 0;
}

// Native host for the system WebView2 runtime. The Python backend owns application state.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <commdlg.h>
#include <shellapi.h>
#include <shlwapi.h>
#include <WebView2.h>
#include "webview2_guids.h"
#include <wrl/client.h>
#include <atomic>
#include <filesystem>
#include <functional>
#include <iostream>
#include <memory>
#include <sstream>
#include <string>
#include <thread>
#include <vector>
#include <json.hpp>

using json = nlohmann::json;
using Microsoft::WRL::ComPtr;
namespace fs = std::filesystem;

static HWND window;
static ComPtr<ICoreWebView2Controller> controller;
static ComPtr<ICoreWebView2> web;
static std::wstring index_file;
static const UINT packet_msg = WM_APP + 1;
static const UINT stop_msg = WM_APP + 2;

static std::wstring wide(const std::string& s) {
    if (s.empty()) return {};
    int n = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, s.data(), int(s.size()), nullptr, 0);
    std::wstring out(n, L'\0');
    if (n) MultiByteToWideChar(CP_UTF8, 0, s.data(), int(s.size()), out.data(), n);
    return out;
}
static std::string utf8(const std::wstring& s) {
    if (s.empty()) return {};
    int n = WideCharToMultiByte(CP_UTF8, 0, s.data(), int(s.size()), nullptr, 0, nullptr, nullptr);
    std::string out(n, '\0');
    WideCharToMultiByte(CP_UTF8, 0, s.data(), int(s.size()), out.data(), n, nullptr, nullptr);
    return out;
}

template<class Interface, class... Args>
class Callback final : public Interface {
    std::atomic<ULONG> references{1};
    std::function<HRESULT(Args...)> function;
public:
    explicit Callback(std::function<HRESULT(Args...)> f) : function(std::move(f)) {}
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void** out) override {
        if (!out) return E_POINTER;
        *out = nullptr;
        if (iid != IID_IUnknown && iid != __uuidof(Interface)) return E_NOINTERFACE;
        *out = static_cast<Interface*>(this);
        AddRef();
        return S_OK;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return ++references; }
    ULONG STDMETHODCALLTYPE Release() override {
        auto n = --references;
        if (!n) delete this;
        return n;
    }
    HRESULT STDMETHODCALLTYPE Invoke(Args... args) override {
        try { return function(args...); }
        catch (...) { return E_FAIL; }
    }
};

template<class Interface, class... Args, class Fun>
ComPtr<Interface> callback(Fun fn) {
    ComPtr<Interface> out;
    out.Attach(new Callback<Interface, Args...>(std::move(fn)));
    return out;
}

static void send(const json& packet) { std::cout << packet.dump() << std::endl; }

static bool is_index(const wchar_t* url) {
    wchar_t path[32768]{};
    DWORD size = DWORD(std::size(path));
    return url && !_wcsnicmp(url, L"file:", 5) && SUCCEEDED(PathCreateFromUrlW(url, path, &size, 0))
        && CompareStringOrdinal(path, -1, index_file.c_str(), -1, TRUE) == CSTR_EQUAL;
}

static std::wstring dialog_filter(const std::wstring& filters) {
    std::wstring out;
    for (size_t pos = 0; pos < filters.size();) {
        size_t end = filters.find(L";;", pos);
        auto label = filters.substr(pos, end == std::wstring::npos ? end : end - pos);
        auto left = label.find(L'('), right = label.rfind(L')');
        if (left != std::wstring::npos && right > left + 1) {
            std::wistringstream patterns(label.substr(left + 1, right - left - 1));
            std::wstring pattern, joined;
            while (patterns >> pattern) {
                if (!joined.empty()) joined += L';';
                joined += pattern == L"*" ? L"*.*" : pattern;
            }
            if (!joined.empty()) {
                out += label; out += L'\0'; out += joined; out += L'\0';
            }
        }
        if (end == std::wstring::npos) break;
        pos = end + 2;
    }
    if (out.empty()) {
        out += L"所有文件"; out += L'\0'; out += L"*.*"; out += L'\0';
    }
    out += L'\0';
    return out;
}

static json select_files(const json& packet, bool save) {
    std::vector<wchar_t> buf(65536);
    auto initial = fs::path(wide(packet.value("path", "")));
    auto dir = initial.parent_path().wstring();
    auto name = initial.filename().wstring();
    if (name.size() < buf.size()) std::copy(name.begin(), name.end(), buf.begin());
    auto filter = dialog_filter(wide(packet.value("filter", "")));
    auto title = wide(packet.value("title", ""));
    OPENFILENAMEW form{};
    form.lStructSize = sizeof(form);
    form.hwndOwner = window;
    form.lpstrFile = buf.data();
    form.nMaxFile = DWORD(buf.size());
    form.lpstrFilter = filter.c_str();
    form.lpstrTitle = title.c_str();
    if (!dir.empty()) form.lpstrInitialDir = dir.c_str();
    form.Flags = OFN_EXPLORER | OFN_PATHMUSTEXIST | OFN_NOCHANGEDIR | OFN_HIDEREADONLY;
    if (save) {
        form.Flags |= OFN_OVERWRITEPROMPT;
        form.lpstrDefExt = L"md";
        if (GetSaveFileNameW(&form)) return utf8(buf.data());
        return nullptr;
    }
    json result = json::array();
    form.Flags |= OFN_FILEMUSTEXIST | OFN_ALLOWMULTISELECT;
    if (GetOpenFileNameW(&form)) {
        fs::path first(buf.data());
        const wchar_t* next = buf.data() + wcslen(buf.data()) + 1;
        if (!*next) result.push_back(utf8(first.wstring()));
        for (; *next; next += wcslen(next) + 1) result.push_back(utf8((first / next).wstring()));
    }
    return result;
}

static void deliver(const json& packet) {
    auto type = packet.value("type", "");
    if (type == "quit") { DestroyWindow(window); return; }
    if (type == "signal" && web) { web->PostWebMessageAsJson(wide(packet.dump()).c_str()); return; }
    if (type != "dialog") return;
    auto kind = packet.value("kind", "");
    json value = nullptr;
    if (kind == "open-files" || kind == "save-file") value = select_files(packet, kind == "save-file");
    else if (kind == "message" || kind == "confirm") {
        auto title = wide(packet.value("title", "")), text = wide(packet.value("message", ""));
        int response = MessageBoxW(window, text.c_str(), title.c_str(), kind == "confirm"
            ? MB_YESNO | MB_ICONQUESTION | MB_DEFBUTTON2 : MB_OK | MB_ICONINFORMATION);
        if (kind == "confirm") value = response == IDYES;
    }
    send({{"type","dialog-result"}, {"id",packet.at("id")}, {"value",value}});
}

static void size_browser() {
    if (!controller) return;
    RECT rect{};
    GetClientRect(window, &rect);
    controller->put_Bounds(rect);
}

static LRESULT CALLBACK window_proc(HWND hwnd, UINT msg, WPARAM wparam, LPARAM lparam) {
    switch (msg) {
        case WM_SIZE: size_browser(); return 0;
        case WM_MOVE: if (controller) controller->NotifyParentWindowPositionChanged(); break;
        case WM_SETFOCUS: if (controller) controller->MoveFocus(COREWEBVIEW2_MOVE_FOCUS_REASON_PROGRAMMATIC); break;
        case WM_DPICHANGED: {
            auto rect = reinterpret_cast<RECT*>(lparam);
            SetWindowPos(hwnd, nullptr, rect->left, rect->top, rect->right-rect->left, rect->bottom-rect->top,
                         SWP_NOZORDER | SWP_NOACTIVATE);
            return 0;
        }
        case WM_CLOSE: send({{"type","close-request"}}); return 0;
        case WM_DESTROY:
            if (controller) controller->Close();
            PostQuitMessage(0);
            return 0;
        case stop_msg: DestroyWindow(hwnd); return 0;
        case packet_msg: {
            std::unique_ptr<json> packet(reinterpret_cast<json*>(lparam));
            try { deliver(*packet); } catch (const json::exception&) {}
            return 0;
        }
    }
    return DefWindowProcW(hwnd, msg, wparam, lparam);
}

static void read_packets(HWND hwnd) {
    std::string line;
    while (std::getline(std::cin, line)) {
        auto parsed = json::parse(line, nullptr, false);
        if (parsed.is_discarded() || !parsed.is_object()) continue;
        auto packet = std::make_unique<json>(std::move(parsed));
        if (!PostMessageW(hwnd, packet_msg, 0, reinterpret_cast<LPARAM>(packet.get()))) break;
        packet.release();
    }
    PostMessageW(hwnd, stop_msg, 0, 0);
}

static HRESULT setup_controller(HRESULT result, ICoreWebView2Controller* created) {
    if (FAILED(result) || !created) { PostMessageW(window, stop_msg, 0, 0); return E_FAIL; }
    controller = created;
    if (FAILED(controller->get_CoreWebView2(&web))) { PostMessageW(window, stop_msg, 0, 0); return E_FAIL; }
    EventRegistrationToken token{};
    auto navigation = callback<ICoreWebView2NavigationStartingEventHandler, ICoreWebView2*, ICoreWebView2NavigationStartingEventArgs*>(
        [](auto*, auto* args) {
            LPWSTR url = nullptr;
            args->get_Uri(&url);
            if (!is_index(url)) args->put_Cancel(TRUE);
            CoTaskMemFree(url);
            return S_OK;
        });
    web->add_NavigationStarting(navigation.Get(), &token);
    auto popup = callback<ICoreWebView2NewWindowRequestedEventHandler, ICoreWebView2*, ICoreWebView2NewWindowRequestedEventArgs*>(
        [](auto*, auto* args) { args->put_Handled(TRUE); return S_OK; });
    web->add_NewWindowRequested(popup.Get(), &token);
    auto message = callback<ICoreWebView2WebMessageReceivedEventHandler, ICoreWebView2*, ICoreWebView2WebMessageReceivedEventArgs*>(
        [](auto*, auto* args) {
            LPWSTR url = nullptr, raw = nullptr;
            args->get_Source(&url);
            bool trusted = is_index(url);
            CoTaskMemFree(url);
            if (trusted && SUCCEEDED(args->get_WebMessageAsJson(&raw)) && raw) {
                auto packet = json::parse(utf8(raw), nullptr, false);
                CoTaskMemFree(raw);
                if (packet.is_object() && (packet.value("type", "") == "ready" || packet.value("type", "") == "command"))
                    send(packet);
            }
            return S_OK;
        });
    web->add_WebMessageReceived(message.Get(), &token);
    ComPtr<ICoreWebView2Settings> settings;
    if (SUCCEEDED(web->get_Settings(&settings))) {
        settings->put_AreHostObjectsAllowed(FALSE);
        settings->put_AreDefaultContextMenusEnabled(FALSE);
        settings->put_IsStatusBarEnabled(FALSE);
    }
    wchar_t url[32768]{};
    DWORD len = DWORD(std::size(url));
    HRESULT hr = UrlCreateFromPathW(index_file.c_str(), url, &len, 0);
    size_browser();
    if (SUCCEEDED(hr)) hr = web->Navigate(url);
    if (FAILED(hr)) PostMessageW(window, stop_msg, 0, 0);
    return hr;
}

static HRESULT runtime_error(HRESULT result) {
    wchar_t message[450]{};
    swprintf(message, std::size(message), L"无法启动系统 Microsoft Edge WebView2 运行时（0x%08X）。\n是否打开微软官方下载页面？", unsigned(result));
    if (MessageBoxW(window, message, L"听序 · WebView2", MB_YESNO | MB_ICONERROR) == IDYES)
        ShellExecuteW(window, L"open", L"https://developer.microsoft.com/microsoft-edge/webview2/", nullptr, nullptr, SW_SHOWNORMAL);
    PostMessageW(window, stop_msg, 0, 0);
    return result;
}

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE, PWSTR, int show) {
    int count = 0;
    auto argv = CommandLineToArgvW(GetCommandLineW(), &count);
    if (!argv || count != 2) return 2;
    auto dll = fs::path(argv[0]).parent_path() / L"WebView2Loader.dll";
    index_file = fs::absolute(argv[1]).lexically_normal().wstring();
    LocalFree(argv);
    if (FAILED(CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED))) return 1;
    SetProcessDPIAware();
    WNDCLASSW cls{};
    cls.hInstance = instance; cls.lpfnWndProc = window_proc; cls.lpszClassName = L"TingsuWebView";
    cls.hCursor = LoadCursorW(nullptr, IDC_ARROW);
    cls.hIcon = LoadIconW(instance, MAKEINTRESOURCEW(1));
    RegisterClassW(&cls);
    RECT area{}; SystemParametersInfoW(SPI_GETWORKAREA, 0, &area, 0);
    int width = std::min(1420, int((area.right-area.left)*0.92));
    int height = std::min(940, int((area.bottom-area.top)*0.90));
    window = CreateWindowExW(0, cls.lpszClassName, L"听序 · 会议工作台", WS_OVERLAPPEDWINDOW,
        area.left+(area.right-area.left-width)/2, area.top+(area.bottom-area.top-height)/2, width, height,
        nullptr, nullptr, instance, nullptr);
    if (!window) return 1;
    ShowWindow(window, show);
    std::thread(read_packets, window).detach();
    HMODULE library = LoadLibraryExW(dll.c_str(), nullptr, LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32);
    auto create = library ? reinterpret_cast<decltype(&CreateCoreWebView2EnvironmentWithOptions)>(
        GetProcAddress(library, "CreateCoreWebView2EnvironmentWithOptions")) : nullptr;
    std::wstring data_dir = _wgetenv(L"LOCALAPPDATA") ? _wgetenv(L"LOCALAPPDATA") : L".";
    data_dir += L"\\Tingsu\\cache\\WebView2";
    auto env_ready = callback<ICoreWebView2CreateCoreWebView2EnvironmentCompletedHandler, HRESULT, ICoreWebView2Environment*>(
        [](HRESULT hr, ICoreWebView2Environment* env) {
            if (FAILED(hr) || !env) return runtime_error(hr);
            auto ready = callback<ICoreWebView2CreateCoreWebView2ControllerCompletedHandler, HRESULT, ICoreWebView2Controller*>(setup_controller);
            hr = env->CreateCoreWebView2Controller(window, ready.Get());
            return FAILED(hr) ? runtime_error(hr) : S_OK;
        });
    HRESULT hr = create ? create(nullptr, data_dir.c_str(), nullptr, env_ready.Get()) : HRESULT_FROM_WIN32(ERROR_MOD_NOT_FOUND);
    if (FAILED(hr)) runtime_error(hr);
    MSG msg{};
    while (GetMessageW(&msg, nullptr, 0, 0) > 0) { TranslateMessage(&msg); DispatchMessageW(&msg); }
    web.Reset(); controller.Reset(); env_ready.Reset();
    CoUninitialize();
    return 0;
}

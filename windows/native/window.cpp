#include "capture.h"
#include <d3d11.h>
#include <dwmapi.h>
#include <roapi.h>
#include <winstring.h>
#ifdef __MINGW32__
// WIDL declares both IReference<BYTE> and IReference<boolean> using unsigned char.
// Neither specialization is used by our capture interfaces.
#define ____FIReference_1_BYTE_INTERFACE_DEFINED__
#endif
#include <cstring>
#include <windows.graphics.capture.interop.h>

namespace Graphics = ABI::Windows::Graphics;
namespace Capture = Graphics::Capture;
namespace Direct3D = Graphics::DirectX::Direct3D11;

extern "C" HRESULT WINAPI CreateDirect3D11DeviceFromDXGIDevice(IDXGIDevice *, IInspectable **);

// ABI from windows.graphics.directx.direct3d11.interop.h, which MinGW omits.
struct SurfaceAccess : IUnknown {
    virtual HRESULT STDMETHODCALLTYPE GetInterface(REFIID, void **) = 0;
};
const GUID surface_access_id = {
    0xa9b3d012, 0x3df2, 0x4ee3, {0xb8, 0xd1, 0x86, 0x95, 0xf4, 0x57, 0xd3, 0xc1}};

static std::wstring window_token(HWND window) {
    DWORD pid = 0;
    if (!GetWindowThreadProcessId(window, &pid))
        return {};
    const auto owner = process_token(pid);
    return owner.empty() ? L"" : std::to_wstring(reinterpret_cast<UINT_PTR>(window)) + L"/" + owner;
}

static BOOL CALLBACK add_window(HWND window, LPARAM) {
    if (!IsWindowVisible(window) || window == GetShellWindow() || IsIconic(window))
        return TRUE;
    DWORD cloaked = 0;
    if (SUCCEEDED(DwmGetWindowAttribute(window, DWMWA_CLOAKED, &cloaked, sizeof(cloaked))) && cloaked)
        return TRUE;
    wchar_t title[1024];
    if (!GetWindowTextW(window, title, static_cast<int>(std::size(title))))
        return TRUE;
    auto token = window_token(window);
    if (!token.empty()) {
        DWORD pid = 0;
        GetWindowThreadProcessId(window, &pid);
        record_json("window", token, std::wstring(title) + L" · " + process_name(pid));
    }
    return TRUE;
}

void list_windows() { EnumWindows(add_window, 0); }

static void check_size(int width, int height) {
    if (width <= 0 || height <= 0 || uint64_t(width) * height > 40'000'000)
        throw std::runtime_error("Invalid window dimensions");
}

static void write_bitmap(int width, int height, const BYTE *pixels, size_t pitch) {
    check_size(width, height);
    BITMAPINFOHEADER info{};
    info.biSize = sizeof(info);
    info.biWidth = width;
    info.biHeight = -height; // top-down BGRA
    info.biPlanes = 1;
    info.biBitCount = 32;
    info.biCompression = BI_RGB;
    info.biSizeImage = DWORD(width * height * 4);
    BITMAPFILEHEADER header{};
    header.bfType = 0x4d42;
    header.bfOffBits = sizeof(header) + sizeof(info);
    header.bfSize = header.bfOffBits + info.biSizeImage;
    write_output(&header, sizeof(header));
    write_output(&info, sizeof(info));
    for (int y = 0; y < height; y++)
        write_output(pixels + y * pitch, size_t(width) * 4);
}

template <class T> static HRESULT factory(PCWSTR name, ComPtr<T> &out) {
    HSTRING_HEADER header{};
    HSTRING h = nullptr;
    HRESULT hr = WindowsCreateStringReference(name, static_cast<UINT32>(wcslen(name)), &header, &h);
    return SUCCEEDED(hr) ? RoGetActivationFactory(h, IID_PPV_ARGS(&out)) : hr;
}

static bool has_compositor_capture() {
    ComPtr<Capture::IGraphicsCaptureSessionStatics> statics;
    HRESULT hr = factory(L"Windows.Graphics.Capture.GraphicsCaptureSession", statics);
    if (hr == REGDB_E_CLASSNOTREG || hr == E_NOINTERFACE || hr == E_NOTIMPL)
        return false;
    check(hr, "Open Windows Graphics Capture");
    boolean supported = false;
    check(statics->IsSupported(&supported), "Check window capture capability");
    return supported;
}

static void capture_composited(HWND window) {
    ComPtr<IGraphicsCaptureItemInterop> interop;
    check(factory(L"Windows.Graphics.Capture.GraphicsCaptureItem", interop), "Open window capture factory");
    ComPtr<Capture::IGraphicsCaptureItem> item;
    check(interop->CreateForWindow(window, IID_PPV_ARGS(&item)), "Open selected capture window");
    Graphics::SizeInt32 size{};
    check(item->get_Size(&size), "Read window size");
    check_size(size.Width, size.Height);

    ComPtr<ID3D11Device> device;
    ComPtr<ID3D11DeviceContext> context;
    HRESULT hr =
        D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, D3D11_CREATE_DEVICE_BGRA_SUPPORT,
                          nullptr, 0, D3D11_SDK_VERSION, &device, nullptr, &context);
    if (FAILED(hr))
        check(D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_WARP, nullptr, D3D11_CREATE_DEVICE_BGRA_SUPPORT,
                                nullptr, 0, D3D11_SDK_VERSION, &device, nullptr, &context),
              "Create Direct3D capture device");
    ComPtr<IDXGIDevice> dxgi;
    check(device.As(&dxgi), "Open DXGI device");
    ComPtr<IInspectable> inspectable;
    check(CreateDirect3D11DeviceFromDXGIDevice(dxgi.Get(), &inspectable), "Create WinRT graphics device");
    ComPtr<Direct3D::IDirect3DDevice> gpu;
    check(inspectable.As(&gpu), "Open WinRT graphics device");

    ComPtr<Capture::IDirect3D11CaptureFramePoolStatics2> factory_pool;
    check(factory(L"Windows.Graphics.Capture.Direct3D11CaptureFramePool", factory_pool),
          "Open window frame pool");
    ComPtr<Capture::IDirect3D11CaptureFramePool> pool;
    check(factory_pool->CreateFreeThreaded(
              gpu.Get(), Graphics::DirectX::DirectXPixelFormat_B8G8R8A8UIntNormalized, 2, size, &pool),
          "Create window frame pool");
    ComPtr<Capture::IGraphicsCaptureSession> session;
    check(pool->CreateCaptureSession(item.Get(), &session), "Create capture session");
    check(session->StartCapture(), "Start window capture");
    ComPtr<Capture::IDirect3D11CaptureFrame> frame;
    auto until = GetTickCount64() + 4000;
    while (!frame && GetTickCount64() < until) {
        check(pool->TryGetNextFrame(&frame), "Read captured frame");
        if (!frame)
            Sleep(15);
    }
    if (!frame)
        throw std::runtime_error("No frame from the selected window; restore the window and try again");
    ComPtr<Direct3D::IDirect3DSurface> surface;
    check(frame->get_Surface(&surface), "Read capture surface");
    ComPtr<SurfaceAccess> access;
    check(surface->QueryInterface(surface_access_id, reinterpret_cast<void **>(access.GetAddressOf())),
          "Read surface interface");
    ComPtr<ID3D11Texture2D> texture;
    check(access->GetInterface(IID_PPV_ARGS(&texture)), "Read window texture");
    D3D11_TEXTURE2D_DESC desc{};
    texture->GetDesc(&desc);
    Graphics::SizeInt32 content{};
    check(frame->get_ContentSize(&content), "Read captured content size");
    int width = std::min<int>(content.Width, desc.Width);
    int height = std::min<int>(content.Height, desc.Height);
    check_size(width, height);
    desc.BindFlags = 0;
    desc.MiscFlags = 0;
    desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    desc.Usage = D3D11_USAGE_STAGING;
    ComPtr<ID3D11Texture2D> readback;
    check(device->CreateTexture2D(&desc, nullptr, &readback), "Create CPU-readable frame");
    context->CopyResource(readback.Get(), texture.Get());
    D3D11_MAPPED_SUBRESOURCE mapped{};
    check(context->Map(readback.Get(), 0, D3D11_MAP_READ, 0, &mapped), "Map window pixels");
    write_bitmap(width, height, static_cast<BYTE *>(mapped.pData), mapped.RowPitch);
    context->Unmap(readback.Get(), 0);
}

// A DC painted only by the selected HWND works on hosts without Windows.Graphics.Capture.
// Do not use a desktop DC or crop the screen: that could include unrelated, overlapping windows.
static void capture_legacy(HWND window) {
    RECT rect{};
    if (!GetWindowRect(window, &rect))
        throw std::runtime_error("Cannot read window bounds");
    int width = rect.right - rect.left, height = rect.bottom - rect.top;
    check_size(width, height);
    HDC dc = CreateCompatibleDC(nullptr);
    if (!dc)
        throw std::runtime_error("Cannot create a capture DC");
    BITMAPINFO info{};
    info.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
    info.bmiHeader.biWidth = width;
    info.bmiHeader.biHeight = -height;
    info.bmiHeader.biPlanes = 1;
    info.bmiHeader.biBitCount = 32;
    void *pixels = nullptr;
    HBITMAP bitmap = CreateDIBSection(dc, &info, DIB_RGB_COLORS, &pixels, nullptr, 0);
    if (!bitmap) {
        DeleteDC(dc);
        throw std::runtime_error("Cannot create window bitmap");
    }
    auto previous = SelectObject(dc, bitmap);
    BOOL rendered = PrintWindow(window, dc, 2 /* PW_RENDERFULLCONTENT */);
    if (!rendered) {
        DWORD_PTR result = 0;
        rendered = SendMessageTimeoutW(window, WM_PRINT, reinterpret_cast<WPARAM>(dc),
                                       PRF_CLIENT | PRF_CHILDREN | PRF_NONCLIENT | PRF_ERASEBKGND,
                                       SMTO_ABORTIFHUNG, 2000, &result) != 0;
    }
    GdiFlush();
    if (rendered)
        write_bitmap(width, height, static_cast<const BYTE *>(pixels), size_t(width) * 4);
    SelectObject(dc, previous);
    DeleteObject(bitmap);
    DeleteDC(dc);
    if (!rendered)
        throw std::runtime_error("Selected window does not support still capture");
}

void capture_window(const std::wstring &token) {
    auto window = reinterpret_cast<HWND>(static_cast<UINT_PTR>(std::stoull(token)));
    if (!IsWindow(window) || window_token(window) != token)
        throw std::runtime_error("Selected window was closed; select it again");
    if (!IsWindowVisible(window) || IsIconic(window))
        throw std::runtime_error("Restore the selected meeting window before capturing it");
    DWORD affinity{};
    if (GetWindowDisplayAffinity(window, &affinity) && affinity != WDA_NONE)
        throw std::runtime_error("This window restricts screen capture");
    if (has_compositor_capture())
        capture_composited(window);
    else
        capture_legacy(window);
}

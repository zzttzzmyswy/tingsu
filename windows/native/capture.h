#pragma once
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <stdexcept>
#include <string>
#include <vector>
#include <wrl/client.h>

using Microsoft::WRL::ComPtr;

inline void check(HRESULT status, const char *operation) {
    if (FAILED(status)) {
        char code[20];
        std::snprintf(code, sizeof(code), " (0x%08lx)", static_cast<unsigned long>(status));
        throw std::runtime_error(std::string(operation) + code);
    }
}

struct Handle {
    HANDLE value;
    explicit Handle(HANDLE h) : value(h) {}
    Handle(const Handle &) = delete;
    ~Handle() {
        if (value)
            CloseHandle(value);
    }
};

std::wstring process_token(DWORD pid);
std::wstring process_name(DWORD pid);
void record_json(const std::string &kind, const std::wstring &id, const std::wstring &label);
void write_output(const void *data, size_t size);
void audio_sources();
void capture_audio(const std::wstring &kind, const std::wstring &id);
void list_windows();
void capture_window(const std::wstring &token);

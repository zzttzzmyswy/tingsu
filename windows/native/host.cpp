// Launch an installed agent outside the frozen GUI's DLL search path.
// The job owns the entire CLI process tree, including Node's ACP workers.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <cstdio>
#include <cstdlib>
#include <string>

static std::wstring quote(const wchar_t *argument) {
    std::wstring result = L"\"";
    size_t slashes = 0;
    for (const wchar_t *p = argument; *p; ++p) {
        if (*p == L'\\') {
            ++slashes;
            continue;
        }
        if (*p == L'"')
            result.append(slashes + 1, L'\\');
        result.append(slashes, L'\\');
        result += *p;
        slashes = 0;
    }
    result.append(2 * slashes, L'\\');
    return result + L'"';
}

static int fail(const char *step) {
    std::fprintf(stderr, "Windows CLI launcher: %s (%lu)\n", step, GetLastError());
    return 126;
}

int wmain(int argc, wchar_t **argv) {
    if (argc < 3) return 126;
    if (!SetDllDirectoryW(nullptr)) return fail("reset DLL search");
    HANDLE parent = OpenProcess(SYNCHRONIZE, FALSE, std::wcstoul(argv[1], nullptr, 10));
    if (!parent) return fail("open application process");
    HANDLE job = CreateJobObjectW(nullptr, nullptr);
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits{};
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    if (!job || !SetInformationJobObject(job, JobObjectExtendedLimitInformation, &limits, sizeof(limits)))
        return fail("configure process job");
    std::wstring line;
    for (int i = 2; i < argc; i++) {
        if (i > 2) line += L' ';
        line += quote(argv[i]);
    }
    STARTUPINFOW startup{};
    startup.cb = sizeof(startup);
    startup.dwFlags = STARTF_USESTDHANDLES;
    startup.hStdInput = GetStdHandle(STD_INPUT_HANDLE);
    startup.hStdOutput = GetStdHandle(STD_OUTPUT_HANDLE);
    startup.hStdError = GetStdHandle(STD_ERROR_HANDLE);
    PROCESS_INFORMATION proc{};
    if (!CreateProcessW(argv[2], line.data(), nullptr, nullptr, TRUE, CREATE_SUSPENDED | CREATE_NO_WINDOW,
                        nullptr, nullptr, &startup, &proc))
        return fail("start executable");
    if (!AssignProcessToJobObject(job, proc.hProcess) || ResumeThread(proc.hThread) == DWORD(-1)) {
        const int code = fail("attach executable to job");
        TerminateProcess(proc.hProcess, code);
        return code;
    }
    CloseHandle(proc.hThread);
    HANDLE wait[] = {proc.hProcess, parent};
    DWORD code = 1;
    if (WaitForMultipleObjects(2, wait, FALSE, INFINITE) == WAIT_OBJECT_0)
        GetExitCodeProcess(proc.hProcess, &code);
    CloseHandle(job);
    CloseHandle(proc.hProcess);
    CloseHandle(parent);
    return static_cast<int>(code);
}

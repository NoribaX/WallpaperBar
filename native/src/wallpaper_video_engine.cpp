#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <dwmapi.h>
#include <shlobj.h>
#include <wrl.h>
#include <WebView2.h>
#include <algorithm>
#include <atomic>
#include <cwctype>
#include <filesystem>
#include <iterator>
#include <memory>
#include <mutex>
#include <string>
#include <system_error>
#include <thread>
#include <vector>

using Microsoft::WRL::Callback;
using Microsoft::WRL::ComPtr;
namespace fs = std::filesystem;
namespace {
constexpr wchar_t kClass[] = L"WallpaperBar.LiveVideo.Surface.v1";
constexpr UINT kStop = WM_APP + 51, kPause = WM_APP + 52, kResume = WM_APP + 53;
constexpr UINT kSpawnDesktopWallpaperLayer = 0x052C;
constexpr UINT_PTR kTimer = 0x5742;
constexpr unsigned kFullscreen = 1, kBattery = 2;
constexpr int kOk = 0, kBadArg = 1, kBusy = 2, kStartFailed = 3;
enum class StartupStage : int {
    desktopSurface,
    webViewEnvironment,
    webViewController,
    videoPage,
    videoFile,
    videoPlayback,
    ready
};
const wchar_t* StartupStageText(StartupStage stage) {
    switch (stage) {
    case StartupStage::desktopSurface: return L"creating the desktop surface";
    case StartupStage::webViewEnvironment: return L"starting the WebView2 runtime";
    case StartupStage::webViewController: return L"creating the WebView2 controller";
    case StartupStage::videoPage: return L"loading the local video page";
    case StartupStage::videoFile: return L"loading video data";
    case StartupStage::videoPlayback: return L"waiting for the video to begin playing";
    case StartupStage::ready: return L"finishing wallpaper startup";
    }
    return L"starting the live wallpaper";
}
std::mutex g_lock, g_errorLock;
std::shared_ptr<class Engine> g_engine;
std::wstring g_error;

void SetError(const std::wstring& e) {
    std::lock_guard<std::mutex> guard(g_errorLock);
    g_error = e;
}
std::wstring LastError() {
    std::lock_guard<std::mutex> guard(g_errorLock);
    return g_error;
}
std::wstring Lower(std::wstring s) {
    std::transform(s.begin(), s.end(), s.begin(),
        [](wchar_t c) { return static_cast<wchar_t>(towlower(c)); });
    return s;
}
struct Monitor {
    HMONITOR handle{};
    RECT rect{};
    std::wstring name;
};
BOOL CALLBACK EnumMonitor(HMONITOR h, HDC, LPRECT, LPARAM p) {
    auto* out = reinterpret_cast<std::vector<Monitor>*>(p);
    MONITORINFOEXW mi{};
    mi.cbSize = sizeof(mi);
    if (GetMonitorInfoW(h, &mi))
        out->push_back({h, mi.rcMonitor, mi.szDevice});
    return TRUE;
}
std::vector<Monitor> Monitors() {
    std::vector<Monitor> out;
    EnumDisplayMonitors(nullptr, nullptr, EnumMonitor, reinterpret_cast<LPARAM>(&out));
    return out;
}
bool SameRect(const RECT& a, const RECT& b) {
    return a.left == b.left && a.top == b.top &&
           a.right == b.right && a.bottom == b.bottom;
}
struct DesktopSearch { HWND icons{}; HWND parent{}; };
BOOL CALLBACK FindIcon(HWND child, LPARAM p) {
    auto* s = reinterpret_cast<DesktopSearch*>(p);
    wchar_t name[128]{};
    if (GetClassNameW(child, name, static_cast<int>(std::size(name))) &&
        wcscmp(name, L"SHELLDLL_DefView") == 0) {
        s->icons = child;
        s->parent = GetParent(child);
        return FALSE;
    }
    return TRUE;
}
BOOL CALLBACK FindDesktop(HWND top, LPARAM p) {
    auto* s = reinterpret_cast<DesktopSearch*>(p);
    EnumChildWindows(top, FindIcon, p);
    return s->icons == nullptr;
}
std::wstring EncodeName(const std::wstring& name) {
    int n = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, name.c_str(),
        static_cast<int>(name.size()), nullptr, 0, nullptr, nullptr);
    if (n <= 0) return {};
    std::string bytes(static_cast<size_t>(n), '\0');
    WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, name.c_str(),
        static_cast<int>(name.size()), bytes.data(), n, nullptr, nullptr);
    constexpr char hex[] = "0123456789ABCDEF";
    std::wstring out;
    for (unsigned char c : bytes) {
        bool safe = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
            (c >= '0' && c <= '9') || c == '-' || c == '_' || c == '.' || c == '~';
        if (safe) out.push_back(static_cast<wchar_t>(c));
        else {
            out.push_back(L'%');
            out.push_back(static_cast<wchar_t>(hex[c >> 4]));
            out.push_back(static_cast<wchar_t>(hex[c & 15]));
        }
    }
    return out;
}
std::wstring Html(const std::wstring& url) {
    std::wstring safe;
    for (wchar_t c : url) {
        if (c == L'&') safe += L"&amp;";
        else if (c == L'"') safe += L"&quot;";
        else safe += c;
    }
    return L"<!doctype html><html><head><meta charset=\"utf-8\">"
        L"<style>html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#000}"
        L"video{width:100%;height:100%;object-fit:cover;display:block}</style></head><body>"
        L"<video id=\"v\" autoplay muted loop playsinline disablepictureinpicture></video>"
        L"<script>let v=document.getElementById('v');window.__p=false;"
        L"chrome.webview.postMessage('WPV_PAGE_READY');"
        L"v.onerror=()=>{let e=v.error;chrome.webview.postMessage('WPV_MEDIA_ERROR:decode code='+(e?e.code:'?')+"
        L"', readyState='+v.readyState+', networkState='+v.networkState);};"
        L"v.onplaying=()=>{if(!window.__r){window.__r=true;chrome.webview.postMessage('WPV_MEDIA_READY');}};"
        L"v.onloadeddata=()=>{chrome.webview.postMessage('WPV_MEDIA_DATA');if(!window.__p)"
        L"v.play().catch(e=>chrome.webview.postMessage('WPV_MEDIA_ERROR:play '+e.name+': '+e.message));};"
        L"v.src=\"" + safe + L"\";v.load();</script></body></html>";
}
std::wstring WebDataFolder() {
    PWSTR local = nullptr;
    if (FAILED(SHGetKnownFolderPath(FOLDERID_LocalAppData, KF_FLAG_CREATE,
                                    nullptr, &local)) || !local) return {};
    fs::path path = fs::path(local) / L"WallpaperBar" / L"WebView2";
    CoTaskMemFree(local);
    std::error_code ec;
    fs::create_directories(path, ec);
    return ec ? L"" : path.wstring();
}

class Engine : public std::enable_shared_from_this<Engine> {
    struct Surface {
        Monitor monitor;
        HWND hwnd{};
        ComPtr<ICoreWebView2Controller> controller;
        ComPtr<ICoreWebView2> view;
        EventRegistrationToken nav{}, message{};
    };
public:
    Engine(std::wstring path, std::wstring target, unsigned flags)
        : path_(std::move(path)), target_(std::move(target)), flags_(flags) {
        ready_ = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    }
    ~Engine() { StopAndJoin(); if (ready_) CloseHandle(ready_); }
    bool Valid() const { return ready_ != nullptr; }
    bool Finished() const { return finished_.load(); }
    bool Running() const { return running_.load(); }
    bool Launch() {
        try {
            auto self = shared_from_this();
            thread_ = std::thread([self] { self->Run(); });
            return true;
        } catch (...) {
            SetError(L"Could not start the native video thread.");
            return false;
        }
    }
    bool WaitReady() {
        if (WaitForSingleObject(ready_, 20000) != WAIT_OBJECT_0) {
            const std::wstring timeout = L"Timed out while " +
                std::wstring(StartupStageText(stage_.load())) + L".";
            RequestStop();
            Join();
            // Run() reports a cancellation while unwinding. Restore the more useful
            // timeout reason after the worker has finished its cleanup.
            SetError(timeout);
            return false;
        }
        return startupOk_.load();
    }
    void RequestStop() {
        stop_.store(true);
        DWORD id = tid_.load();
        if (id) PostThreadMessageW(id, kStop, 0, 0);
    }
    void StopAndJoin() { RequestStop(); Join(); }
    void Join() {
        std::lock_guard<std::mutex> guard(joinLock_);
        if (thread_.joinable() && thread_.get_id() != std::this_thread::get_id())
            thread_.join();
    }
    bool Pause(bool value) {
        if (!running_.load()) return false;
        DWORD id = tid_.load();
        return id && PostThreadMessageW(id, value ? kPause : kResume, 0, 0);
    }
    void Error(const std::wstring& value) {
        SetError(value);
    }

private:
    void Signal(bool ok) {
        bool expected = false;
        if (signaled_.compare_exchange_strong(expected, true)) {
            startupOk_.store(ok);
            SetEvent(ready_);
        }
    }
    void Fail(const std::wstring& why) {
        Error(why);
        Signal(false);
        for (auto& s : surfaces_) if (s->hwnd) ShowWindow(s->hwnd, SW_HIDE);
        DWORD id = tid_.load();
        if (id) PostThreadMessageW(id, kStop, 0, 0);
    }
    void Run() {
        MSG msg{};
        PeekMessageW(&msg, nullptr, WM_USER, WM_USER, PM_NOREMOVE);
        tid_.store(GetCurrentThreadId());
        DPI_AWARENESS_CONTEXT oldDpi =
            SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
        HRESULT com = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
        bool uninit = SUCCEEDED(com);
        if (FAILED(com) && com != RPC_E_CHANGED_MODE) {
            Error(L"Could not initialize COM for WebView2.");
            stop_.store(true);
            Signal(false);
        } else if (!stop_.load()) {
            stage_.store(StartupStage::desktopSurface);
            if (!CreateSurfaces()) {
                stop_.store(true);
                Signal(false);
            } else {
                stage_.store(StartupStage::webViewEnvironment);
                StartWebView();
            }
        }
        if (stop_.load()) Shutdown();
        while (GetMessageW(&msg, nullptr, 0, 0) > 0) {
            if (!msg.hwnd && msg.message == kStop) { Shutdown(); continue; }
            if (!msg.hwnd && msg.message == kPause) { userPaused_ = true; ApplyPause(); continue; }
            if (!msg.hwnd && msg.message == kResume) { userPaused_ = false; ApplyPause(); continue; }
            TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
        Shutdown();
        CloseViews();
        if (uninit) CoUninitialize();
        if (oldDpi) SetThreadDpiAwarenessContext(oldDpi);
        if (!signaled_.load()) {
            Error(L"Video startup was cancelled.");
            Signal(false);
        }
        running_.store(false);
        finished_.store(true);
    }
    bool CreateSurfaces() {
        HWND progman = FindWindowW(L"Progman", nullptr);
        const bool raisedProgman = progman &&
            (GetWindowLongPtrW(progman, GWL_EXSTYLE) & WS_EX_NOREDIRECTIONBITMAP);
        if (raisedProgman) {
            DWORD_PTR result = 0;
            SendMessageTimeoutW(progman, kSpawnDesktopWallpaperLayer, 0xD, 0x1,
                SMTO_NORMAL, 1000, &result);
        }

        if (raisedProgman) {
            // On the raised desktop, use the live icon view under Progman directly.
            // Enumerating every top-level window can select a hidden shell view after
            // the first wallpaper has been stopped and another is started.
            parent_ = progman;
            icons_ = FindWindowExW(progman, nullptr, L"SHELLDLL_DefView", nullptr);
            if (!icons_) {
                Error(L"Could not find the active desktop icon surface under Progman.");
                return false;
            }
        } else {
            DesktopSearch desktop;
            EnumWindows(FindDesktop, reinterpret_cast<LPARAM>(&desktop));
            if (!desktop.icons || !desktop.parent ||
                !IsWindow(desktop.icons) || !IsWindow(desktop.parent)) {
                Error(L"Could not find the Windows desktop icon surface.");
                return false;
            }
            icons_ = desktop.icons;
            parent_ = desktop.parent;
        }
        raisedDesktop_ =
            (GetWindowLongPtrW(parent_, GWL_EXSTYLE) & WS_EX_NOREDIRECTIONBITMAP) != 0;
        if (raisedDesktop_) {
            worker_ = FindWindowExW(parent_, nullptr, L"WorkerW", nullptr);
            if (!worker_) {
                Error(L"Windows' raised desktop wallpaper layer could not be found.");
                return false;
            }
        }
        auto all = Monitors();
        if (all.empty()) { Error(L"No active monitors found."); return false; }
        if (Lower(target_) == L"all") selected_ = all;
        else for (const auto& m : all)
            if (_wcsicmp(m.name.c_str(), target_.c_str()) == 0) selected_.push_back(m);
        if (selected_.empty()) { Error(L"Requested monitor is not connected."); return false; }
        snapshot_ = all;

        WNDCLASSEXW wc{};
        wc.cbSize = sizeof(wc);
        wc.lpfnWndProc = WindowProc;
        wc.hInstance = GetModuleHandleW(nullptr);
        wc.lpszClassName = kClass;
        wc.hCursor = LoadCursorW(nullptr, IDC_ARROW);
        if (!RegisterClassExW(&wc) && GetLastError() != ERROR_CLASS_ALREADY_EXISTS) {
            Error(L"Could not register desktop video window.");
            return false;
        }
        registered_ = true;

        for (const auto& m : selected_) {
            POINT p[2]{{m.rect.left, m.rect.top}, {m.rect.right, m.rect.bottom}};
            MapWindowPoints(HWND_DESKTOP, parent_, p, 2);
            int w = p[1].x - p[0].x, h = p[1].y - p[0].y;
            if (w <= 0 || h <= 0) { Error(L"Invalid display bounds."); return false; }
            auto s = std::make_unique<Surface>();
            s->monitor = m;
            DWORD exStyle = WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW;
            if (raisedDesktop_) exStyle |= WS_EX_LAYERED;
            s->hwnd = CreateWindowExW(exStyle,
                kClass, L"WallpaperBar Live Video",
                WS_CHILD | WS_CLIPCHILDREN | WS_CLIPSIBLINGS,
                p[0].x, p[0].y, w, h, parent_, nullptr, GetModuleHandleW(nullptr), this);
            if (!s->hwnd) {
                const DWORD error = GetLastError();
                Error(L"Could not create a desktop child surface (Windows error " +
                      std::to_wstring(error) + L").");
                return false;
            }
            if (raisedDesktop_ &&
                !SetLayeredWindowAttributes(s->hwnd, 0, 255, LWA_ALPHA)) {
                const DWORD error = GetLastError();
                Error(L"Could not enable desktop layer composition (Windows error " +
                      std::to_wstring(error) + L").");
                DestroyWindow(s->hwnd);
                return false;
            }
            if (!SetWindowPos(s->hwnd, icons_, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER)) {
                const DWORD error = GetLastError();
                Error(L"Could not position the desktop video surface (Windows error " +
                      std::to_wstring(error) + L").");
                DestroyWindow(s->hwnd);
                return false;
            }
            surfaces_.push_back(std::move(s));
        }
        return true;
    }
    void StartWebView() {
        std::wstring data = WebDataFolder();
        if (data.empty()) { Fail(L"Could not create WebView2 data folder."); return; }
        HRESULT hr = CreateCoreWebView2EnvironmentWithOptions(nullptr, data.c_str(), nullptr,
            Callback<ICoreWebView2CreateCoreWebView2EnvironmentCompletedHandler>(
                [this](HRESULT result, ICoreWebView2Environment* env) -> HRESULT {
                    if (FAILED(result) || !env) {
                        Fail(L"WebView2 Runtime is missing or could not start.");
                        return S_OK;
                    }
                    env_ = env;
                    CreateControllers();
                    return S_OK;
                }).Get());
        if (FAILED(hr)) Fail(L"Could not create WebView2 environment.");
    }
    void CreateControllers() {
        stage_.store(StartupStage::webViewController);
        pending_ = static_cast<int>(surfaces_.size());
        for (auto& holder : surfaces_) {
            Surface* s = holder.get();
            HRESULT hr = env_->CreateCoreWebView2Controller(s->hwnd,
                Callback<ICoreWebView2CreateCoreWebView2ControllerCompletedHandler>(
                    [this, s](HRESULT result, ICoreWebView2Controller* c) -> HRESULT {
                        ControllerCreated(s, result, c);
                        return S_OK;
                    }).Get());
            if (FAILED(hr)) { Fail(L"Could not create WebView2 controller."); return; }
        }
    }
    void ControllerCreated(Surface* s, HRESULT result, ICoreWebView2Controller* c) {
        if (FAILED(result) || !c || stop_.load()) {
            if (!stop_.load()) Fail(L"WebView2 controller initialization failed.");
            return;
        }
        s->controller = c;
        stage_.store(StartupStage::videoPage);
        HRESULT hr = c->get_CoreWebView2(&s->view);
        if (FAILED(hr) || !s->view) { Fail(L"Could not get WebView2 interface."); return; }
        ComPtr<ICoreWebView2Settings> settings;
        hr = s->view->get_Settings(&settings);
        if (FAILED(hr) || !settings) { Fail(L"Could not configure WebView2."); return; }
        settings->put_IsScriptEnabled(TRUE);
        settings->put_AreDefaultContextMenusEnabled(FALSE);
        settings->put_AreDefaultScriptDialogsEnabled(FALSE);
        settings->put_IsZoomControlEnabled(FALSE);
        settings->put_IsWebMessageEnabled(TRUE);

        fs::path media(path_);
        std::wstring file = EncodeName(media.filename().wstring());
        if (file.empty()) { Fail(L"Invalid video filename."); return; }
        ComPtr<ICoreWebView2_3> view3;
        hr = s->view.As(&view3);
        if (FAILED(hr) || !view3) { Fail(L"WebView2 virtual host mapping is unavailable."); return; }
        hr = view3->SetVirtualHostNameToFolderMapping(
            L"wallpaperbar-video.local", media.parent_path().c_str(),
            COREWEBVIEW2_HOST_RESOURCE_ACCESS_KIND_ALLOW);
        if (FAILED(hr)) { Fail(L"Could not map the video folder in WebView2."); return; }

        hr = s->view->add_WebMessageReceived(
            Callback<ICoreWebView2WebMessageReceivedEventHandler>(
                [this](ICoreWebView2*, ICoreWebView2WebMessageReceivedEventArgs* args) -> HRESULT {
                    LPWSTR raw = nullptr;
                    if (SUCCEEDED(args->TryGetWebMessageAsString(&raw)) && raw) {
                        std::wstring msg(raw); CoTaskMemFree(raw);
                        if (msg == L"WPV_PAGE_READY")
                            stage_.store(StartupStage::videoFile);
                        else if (msg == L"WPV_MEDIA_DATA")
                            stage_.store(StartupStage::videoPlayback);
                        else if (msg.compare(0, 16, L"WPV_MEDIA_ERROR:") == 0)
                            Fail(L"Video playback error: " + msg.substr(16));
                        else if (msg == L"WPV_MEDIA_READY" &&
                                 ++mediaReady_ == static_cast<int>(surfaces_.size()) &&
                                 completed_ == pending_) Ready();
                    }
                    return S_OK;
                }).Get(), &s->message);
        if (FAILED(hr)) { Fail(L"Could not register playback error handler."); return; }

        hr = s->view->add_NavigationCompleted(
            Callback<ICoreWebView2NavigationCompletedEventHandler>(
                [this](ICoreWebView2*, ICoreWebView2NavigationCompletedEventArgs* args) -> HRESULT {
                    BOOL ok = FALSE;
                    if (FAILED(args->get_IsSuccess(&ok)) || !ok) {
                        Fail(L"Could not load the local video page.");
                        return S_OK;
                    }
                    ++completed_;
                    if (completed_ == pending_ &&
                        mediaReady_ == static_cast<int>(surfaces_.size())) Ready();
                    return S_OK;
                }).Get(), &s->nav);
        if (FAILED(hr)) { Fail(L"Could not register navigation handler."); return; }

        RECT bounds{}; GetClientRect(s->hwnd, &bounds);
        s->controller->put_Bounds(bounds);
        // Keep WebView2 active while it initializes. A hidden controller can
        // throttle page tasks and prevent the playback-ready event from firing.
        s->controller->put_IsVisible(TRUE);
        std::wstring url = L"https://wallpaperbar-video.local/" + file;
        stage_.store(StartupStage::videoPage);
        hr = s->view->NavigateToString(Html(url).c_str());
        if (FAILED(hr)) Fail(L"WebView2 rejected the video page.");
    }
    void Ready() {
        if (stop_.load()) return;
        for (auto& s : surfaces_) {
            HRESULT hr = s->controller->put_IsVisible(TRUE);
            if (FAILED(hr)) { Fail(L"Could not show the WebView2 wallpaper surface."); return; }
            if (!SetWindowPos(s->hwnd, icons_, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE |
                SWP_NOOWNERZORDER | SWP_SHOWWINDOW)) {
                const DWORD error = GetLastError();
                Fail(L"Could not show the desktop video surface (Windows error " +
                     std::to_wstring(error) + L").");
                return;
            }
            if (!(GetWindowLongPtrW(s->hwnd, GWL_STYLE) & WS_VISIBLE)) {
                Fail(L"Windows did not mark the desktop video surface visible.");
                return;
            }
        }
        if (raisedDesktop_ && !surfaces_.empty() &&
            !SetWindowPos(worker_, surfaces_.front()->hwnd, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER)) {
            const DWORD error = GetLastError();
            Fail(L"Could not place the Windows wallpaper layer behind the video "
                 L"surface (Windows error " + std::to_wstring(error) + L").");
            return;
        }
        if (!surfaces_.empty()) SetTimer(surfaces_[0]->hwnd, kTimer, 1000, nullptr);
        stage_.store(StartupStage::ready);
        running_.store(true);
        Signal(true);
        UpdateAutoPause();
    }
    void OnTimer(HWND hwnd) {
        if (surfaces_.empty() || hwnd != surfaces_[0]->hwnd) return;
        auto now = Monitors();
        if (now.size() != snapshot_.size()) { Fail(L"Monitor layout changed; restart playback."); return; }
        for (const auto& old : snapshot_) {
            auto it = std::find_if(now.begin(), now.end(), [&](const Monitor& m) {
                return _wcsicmp(m.name.c_str(), old.name.c_str()) == 0;
            });
            if (it == now.end() || !SameRect(it->rect, old.rect)) {
                Fail(L"Monitor layout changed; restart playback.");
                return;
            }
        }
        UpdateAutoPause();
    }
    bool FullscreenOn(const Monitor& monitor) {
        HWND w = GetForegroundWindow();
        if (!w) return false;
        w = GetAncestor(w, GA_ROOT);
        if (!w || w == GetDesktopWindow()) return false;
        // Explorer's desktop surfaces cover the monitor, but are not full-screen apps.
        if (w == parent_ || w == worker_ || w == icons_) return false;
        wchar_t className[64]{};
        if (GetClassNameW(w, className, static_cast<int>(std::size(className))) &&
            (_wcsicmp(className, L"Progman") == 0 ||
             _wcsicmp(className, L"WorkerW") == 0 ||
             _wcsicmp(className, L"Shell_TrayWnd") == 0)) return false;
        DWORD pid = 0;
        GetWindowThreadProcessId(w, &pid);
        if (pid == GetCurrentProcessId() || !IsWindowVisible(w) || IsIconic(w)) return false;
        DWORD cloaked = 0;
        if (SUCCEEDED(DwmGetWindowAttribute(w, DWMWA_CLOAKED, &cloaked, sizeof(cloaked))) && cloaked)
            return false;
        HMONITOR h = MonitorFromWindow(w, MONITOR_DEFAULTTONEAREST);
        MONITORINFOEXW mi{}; mi.cbSize = sizeof(mi);
        if (!h || !GetMonitorInfoW(h, &mi) || _wcsicmp(mi.szDevice, monitor.name.c_str()) != 0)
            return false;
        RECT r{}; if (!GetWindowRect(w, &r)) return false;
        constexpr LONG pad = 2;
        return r.left <= monitor.rect.left + pad && r.top <= monitor.rect.top + pad &&
               r.right >= monitor.rect.right - pad && r.bottom >= monitor.rect.bottom - pad;
    }
    void UpdateAutoPause() {
        bool pause = false;
        if (flags_ & kFullscreen)
            for (const auto& m : selected_) if (FullscreenOn(m)) { pause = true; break; }
        if (!pause && (flags_ & kBattery)) {
            SYSTEM_POWER_STATUS p{};
            if (GetSystemPowerStatus(&p) && p.ACLineStatus == 0) pause = true;
        }
        if (pause != autoPaused_) { autoPaused_ = pause; ApplyPause(); }
    }
    void ApplyPause() {
        const wchar_t* script = (userPaused_ || autoPaused_)
            ? L"(function(){window.__p=true;let v=document.getElementById('v');if(v)v.pause();})();"
            : L"(function(){window.__p=false;let v=document.getElementById('v');if(v)v.play().catch(()=>{});})();";
        for (auto& s : surfaces_) if (s->view) s->view->ExecuteScript(script, nullptr);
    }
    void CloseViews() {
        for (auto& s : surfaces_) {
            if (s->view) {
                s->view->remove_WebMessageReceived(s->message);
                s->view->remove_NavigationCompleted(s->nav);
                s->view.Reset();
            }
            if (s->controller) { s->controller->Close(); s->controller.Reset(); }
        }
        env_.Reset();
    }
    void Shutdown() {
        bool expected = false;
        if (!stopping_.compare_exchange_strong(expected, true)) return;
        running_.store(false);
        for (auto& s : surfaces_) if (s->hwnd) KillTimer(s->hwnd, kTimer);
        CloseViews();
        for (auto& s : surfaces_) if (s->hwnd && IsWindow(s->hwnd)) DestroyWindow(s->hwnd);
        if (registered_) { UnregisterClassW(kClass, GetModuleHandleW(nullptr)); registered_ = false; }
        PostQuitMessage(0);
    }
    static LRESULT CALLBACK WindowProc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp) {
        auto* self = reinterpret_cast<Engine*>(GetWindowLongPtrW(hwnd, GWLP_USERDATA));
        if (msg == WM_NCCREATE) {
            auto* cs = reinterpret_cast<CREATESTRUCTW*>(lp);
            self = static_cast<Engine*>(cs->lpCreateParams);
            SetWindowLongPtrW(hwnd, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(self));
        }
        if (self) {
            if (msg == WM_TIMER && wp == kTimer) { self->OnTimer(hwnd); return 0; }
            if (msg == WM_ERASEBKGND) return 1;
            if (msg == WM_DESTROY && !self->stopping_.load())
                self->Fail(L"Explorer destroyed the wallpaper host surface.");
        }
        return DefWindowProcW(hwnd, msg, wp, lp);
    }

    std::wstring path_, target_;
    unsigned flags_{};
    HANDLE ready_{};
    std::thread thread_;
    std::mutex joinLock_;
    std::atomic<DWORD> tid_{0};
    std::atomic<StartupStage> stage_{StartupStage::desktopSurface};
    std::atomic<bool> stop_{false}, stopping_{false}, running_{false};
    std::atomic<bool> finished_{false}, signaled_{false}, startupOk_{false};
    bool userPaused_{}, autoPaused_{}, registered_{};
    int pending_{}, completed_{}, mediaReady_{};
    HWND icons_{}, parent_{};
    HWND worker_{};
    bool raisedDesktop_{};
    std::vector<Monitor> selected_, snapshot_;
    std::vector<std::unique_ptr<Surface>> surfaces_;
    ComPtr<ICoreWebView2Environment> env_;
};
} // namespace

extern "C" __declspec(dllexport)
int __cdecl WPV_StartW(const wchar_t* path, const wchar_t* target, unsigned flags) {
    if (!path || !*path || (flags & ~3u)) { SetError(L"Invalid video path or flags."); return kBadArg; }
    fs::path file(path);
    std::wstring ext = Lower(file.extension().wstring());
    if (ext != L".mp4" && ext != L".webm") { SetError(L"Choose an MP4 or WebM file."); return kBadArg; }
    DWORD attr = GetFileAttributesW(path);
    if (attr == INVALID_FILE_ATTRIBUTES || (attr & FILE_ATTRIBUTE_DIRECTORY)) {
        SetError(L"Video file does not exist."); return kBadArg;
    }
    std::shared_ptr<Engine> stale;
    {
        std::lock_guard<std::mutex> guard(g_lock);
        if (g_engine && !g_engine->Finished()) { SetError(L"A live wallpaper is already running."); return kBusy; }
        stale = std::move(g_engine);
    }
    if (stale) stale->Join();
    auto engine = std::make_shared<Engine>(path, target && *target ? target : L"all", flags);
    if (!engine->Valid()) { SetError(L"Could not create startup event."); return kStartFailed; }
    {
        std::lock_guard<std::mutex> guard(g_lock);
        if (g_engine) { SetError(L"A live wallpaper is already starting."); return kBusy; }
        g_engine = engine;
        if (!engine->Launch()) { g_engine.reset(); return kStartFailed; }
    }
    if (!engine->WaitReady()) {
        engine->StopAndJoin();
        std::lock_guard<std::mutex> guard(g_lock);
        if (g_engine == engine) g_engine.reset();
        return kStartFailed;
    }
    SetError(L"");
    return kOk;
}
extern "C" __declspec(dllexport)
int __cdecl WPV_Stop() {
    std::shared_ptr<Engine> e;
    { std::lock_guard<std::mutex> guard(g_lock); e = g_engine; }
    if (!e) return kOk;
    e->StopAndJoin();
    { std::lock_guard<std::mutex> guard(g_lock); if (g_engine == e) g_engine.reset(); }
    return kOk;
}
extern "C" __declspec(dllexport)
int __cdecl WPV_Pause() {
    std::shared_ptr<Engine> e;
    { std::lock_guard<std::mutex> guard(g_lock); e = g_engine; }
    if (e && e->Pause(true)) return kOk;
    SetError(L"Live wallpaper is not running."); return kBusy;
}
extern "C" __declspec(dllexport)
int __cdecl WPV_Resume() {
    std::shared_ptr<Engine> e;
    { std::lock_guard<std::mutex> guard(g_lock); e = g_engine; }
    if (e && e->Pause(false)) return kOk;
    SetError(L"Live wallpaper is not running."); return kBusy;
}
extern "C" __declspec(dllexport)
size_t __cdecl WPV_GetMonitorsW(wchar_t* buffer, size_t capacity) {
    auto monitors = Monitors();
    std::wstring text;
    for (size_t i = 0; i < monitors.size(); ++i) {
        if (i) text += L"\n";
        text += monitors[i].name;
    }
    size_t needed = text.size() + 1;
    if (buffer && capacity) wcsncpy_s(buffer, capacity, text.c_str(), _TRUNCATE);
    return needed;
}
extern "C" __declspec(dllexport)
size_t __cdecl WPV_GetLastErrorW(wchar_t* buffer, size_t capacity) {
    auto text = LastError();
    size_t needed = text.size() + 1;
    if (buffer && capacity) wcsncpy_s(buffer, capacity, text.c_str(), _TRUNCATE);
    return needed;
}

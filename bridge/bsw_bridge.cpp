// C bridge to Bambu Studio's closed networking plugin.
//
// The plugin's ABI is C++ (std::string by value, std::function callbacks), so
// ctypes cannot reach it directly. This shim exposes plain C entry points and
// keeps the C++ objects alive on this side of the boundary.
//
// Bring-up order matters and mirrors Studio: create_agent, set_config_dir,
// the callback battery, init_log, start. Skipping the callbacks and calling
// start anyway segfaults the plugin, because it invokes a null std::function.
//
// Build:
//   clang++ -std=c++17 -O2 -shared -fPIC -o libbswbridge.dylib bsw_bridge.cpp
//
// It loads the user's own installed plugin; nothing is redistributed.

#include <dlfcn.h>

#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <functional>
#include <map>
#include <mutex>
#include <thread>
#include <string>
#include <utility>

namespace {

void* g_lib = nullptr;
std::string g_last_error;
std::string g_last_message;
std::string g_buffer;
int g_message_count = 0;
int g_connected = 0;

typedef std::string (*get_version_fn)(void);
typedef void* (*create_agent_fn)(std::string);
typedef int (*destroy_agent_fn)(void*);
typedef int (*init_log_fn)(void*);
typedef int (*start_fn)(void*);
typedef int (*set_config_dir_fn)(void*, std::string);

typedef std::function<void(std::string, std::string)> OnMessageFn;
typedef std::function<void(int, std::string, std::string)> OnLocalConnectedFn;
typedef std::function<void(int, int)> OnServerConnectedFn;
typedef std::function<void(std::string)> OnPrinterConnectedFn;
typedef std::function<void(std::function<void()>)> QueueOnMainFn;
typedef std::function<std::string()> GetCountryCodeFn;
typedef std::function<void(std::string)> OnMsgArrivedFn;
typedef bool (*start_discovery_fn)(void*, bool, bool);
typedef int (*get_camera_url_fn)(void*, std::string, std::function<void(std::string)>);

std::mutex g_camera_mutex;
std::condition_variable g_camera_cv;
std::string g_camera_url;
bool g_camera_ready = false;

bool load_plugin(const char* path) {
    if (g_lib != nullptr) {
        return true;
    }
    g_lib = dlopen(path, RTLD_NOW | RTLD_GLOBAL);
    if (g_lib == nullptr) {
        const char* err = dlerror();
        g_last_error = err != nullptr ? err : "dlopen failed";
        return false;
    }
    return true;
}

template <typename Fn>
Fn symbol(const char* name) {
    if (g_lib == nullptr) {
        return nullptr;
    }
    return reinterpret_cast<Fn>(dlsym(g_lib, name));
}

template <typename Setter, typename Callback>
int install(void* agent, const char* name, Callback&& cb) {
    auto fn = symbol<Setter>(name);
    if (fn == nullptr) {
        return -1;
    }
    return fn(agent, std::function<std::decay_t<Callback>>(std::forward<Callback>(cb)));
}

}  // namespace

extern "C" {

const char* bsw_last_error(void) { return g_last_error.c_str(); }
const char* bsw_last_message(void) { return g_last_message.c_str(); }
int bsw_message_count(void) { return g_message_count; }
int bsw_connected(void) { return g_connected; }
const char* bsw_buffer(void) { return g_buffer.c_str(); }
void bsw_clear_buffer(void) { g_buffer.clear(); }

int bsw_buffer_contains(const char* needle) {
    if (needle == nullptr) return 0;
    return g_buffer.find(needle) != std::string::npos ? 1 : 0;
}

typedef int (*connect_printer_fn)(void*, std::string, std::string, std::string,
                                  std::string, bool);
typedef int (*send_to_printer_fn)(void*, std::string, std::string, int, int);

int bsw_connect(void* agent, const char* dev_id, const char* ip, const char* user,
                const char* pass, int use_ssl) {
    auto fn = symbol<connect_printer_fn>("bambu_network_connect_printer");
    if (fn == nullptr || agent == nullptr) {
        g_last_error = "bambu_network_connect_printer not found";
        return -1;
    }
    return fn(agent, std::string(dev_id), std::string(ip), std::string(user),
              std::string(pass), use_ssl != 0);
}

// The cloud/general message path; send_message_to_printer is the local one.
int bsw_send_message(void* agent, const char* dev_id, const char* json, int qos, int flag) {
    auto fn = symbol<send_to_printer_fn>("bambu_network_send_message");
    if (fn == nullptr || agent == nullptr) {
        g_last_error = "bambu_network_send_message not found";
        return -1;
    }
    return fn(agent, std::string(dev_id), std::string(json), qos, flag);
}

int bsw_send(void* agent, const char* dev_id, const char* json, int qos, int flag) {
    auto fn = symbol<send_to_printer_fn>("bambu_network_send_message_to_printer");
    if (fn == nullptr || agent == nullptr) {
        g_last_error = "bambu_network_send_message_to_printer not found";
        return -1;
    }
    return fn(agent, std::string(dev_id), std::string(json), qos, flag);
}

const char* bsw_plugin_version(const char* dylib_path) {
    static std::string result;
    if (!load_plugin(dylib_path)) {
        return nullptr;
    }
    auto fn = symbol<get_version_fn>("bambu_network_get_version");
    if (fn == nullptr) {
        g_last_error = "bambu_network_get_version not found";
        return nullptr;
    }
    result = fn();
    return result.c_str();
}

void* bsw_create_agent(const char* dylib_path, const char* log_dir,
                       const char* config_dir) {
    if (!load_plugin(dylib_path)) {
        return nullptr;
    }
    auto create = symbol<create_agent_fn>("bambu_network_create_agent");
    if (create == nullptr) {
        g_last_error = "bambu_network_create_agent not found";
        return nullptr;
    }
    void* agent = create(std::string(log_dir));
    if (agent == nullptr) {
        g_last_error = "create_agent returned null";
        return nullptr;
    }
    if (config_dir != nullptr && config_dir[0] != '\0') {
        if (auto set_dir = symbol<set_config_dir_fn>("bambu_network_set_config_dir")) {
            set_dir(agent, std::string(config_dir));
        }
    }
    return agent;
}

// Register the callbacks the plugin dereferences during start(). Every one is
// a no-op except the message hook, which records the last payload so Python
// can prove the link is alive.
// Studio runs this before any cloud call that carries proof-of-possession
// headers; without it the ttcode mint never answers.
// Studio refreshes this constantly before a live view: it installs the app
// certificate on the printer (MQTT `security.app_cert_install`) and harvests
// the printer certificate. The camera mint stays silent without it.
int bsw_install_device_cert(void* agent, const char* dev_id, int lan_only) {
    typedef void (*fn_t)(void*, std::string, bool);
    auto fn = symbol<fn_t>("bambu_network_install_device_cert");
    if (fn == nullptr || agent == nullptr) {
        g_last_error = "bambu_network_install_device_cert not found";
        return -1;
    }
    fn(agent, std::string(dev_id), lan_only != 0);
    return 0;
}

int bsw_check_cert(void* agent) {
    typedef int (*fn_t)(void*);
    auto fn = symbol<fn_t>("bambu_network_check_cert");
    if (fn == nullptr || agent == nullptr) {
        g_last_error = "bambu_network_check_cert not found";
        return -1;
    }
    return fn(agent);
}

int bsw_install_callbacks(void* agent) {
    if (agent == nullptr) {
        return -1;
    }
    int installed = 0;

    if (auto fn = symbol<int (*)(void*, QueueOnMainFn)>("bambu_network_set_queue_on_main_fn")) {
        // Studio posts this work to its GUI event loop and returns at once.
        // Running it inline here deadlocks whenever the plugin holds a lock
        // across the call, which is exactly how the camera mint goes silent.
        fn(agent, QueueOnMainFn([](std::function<void()> work) {
               if (work) {
                   std::thread([work]() mutable { work(); }).detach();
               }
           }));
        ++installed;
    }
    if (auto fn = symbol<int (*)(void*, OnMessageFn)>("bambu_network_set_on_message_fn")) {
        fn(agent, OnMessageFn([](std::string dev_id, std::string msg) {
               g_last_message = msg;
               ++g_message_count;
               if (g_buffer.size() < 400000) { g_buffer += msg; g_buffer += "\n"; }
           }));
        ++installed;
    }
    if (auto fn = symbol<int (*)(void*, OnMessageFn)>(
            "bambu_network_set_on_local_message_fn")) {
        fn(agent, OnMessageFn([](std::string dev_id, std::string msg) {
               g_last_message = msg;
               ++g_message_count;
               if (g_buffer.size() < 400000) { g_buffer += msg; g_buffer += "\n"; }
           }));
        ++installed;
    }
    if (auto fn = symbol<int (*)(void*, OnLocalConnectedFn)>(
            "bambu_network_set_on_local_connect_fn")) {
        fn(agent, OnLocalConnectedFn([](int status, std::string, std::string) {
               g_connected = status;
           }));
        ++installed;
    }
    if (auto fn = symbol<int (*)(void*, OnPrinterConnectedFn)>(
            "bambu_network_set_on_printer_connected_fn")) {
        fn(agent, OnPrinterConnectedFn([](std::string) { g_connected = 1; }));
        ++installed;
    }
    if (auto fn = symbol<int (*)(void*, OnServerConnectedFn)>(
            "bambu_network_set_on_server_connected_fn")) {
        fn(agent, OnServerConnectedFn([](int, int) {}));
        ++installed;
    }
    if (auto fn = symbol<int (*)(void*, OnMsgArrivedFn)>("bambu_network_set_on_ssdp_msg_fn")) {
        fn(agent, OnMsgArrivedFn([](std::string msg) {
               ++g_message_count;
               if (g_buffer.size() < 400000) { g_buffer += msg; g_buffer += "\n"; }
               g_last_message = msg;
           }));
        ++installed;
    }
    if (auto fn = symbol<int (*)(void*, GetCountryCodeFn)>(
            "bambu_network_set_get_country_code_fn")) {
        fn(agent, GetCountryCodeFn([]() { return std::string("US"); }));
        ++installed;
    }
    return installed;
}

// Mints a camera URL (LAN or TUTK) through the plugin's authenticated cloud
// session. packed_dev_id is "<serial>|<dev_ver>|<protocols_json>".
int bsw_camera_url(void* agent, const char* packed_dev_id, int timeout_ms) {
    auto fn = symbol<get_camera_url_fn>("bambu_network_get_camera_url");
    if (fn == nullptr || agent == nullptr) {
        g_last_error = "bambu_network_get_camera_url not found";
        return -1;
    }

    {
        std::lock_guard<std::mutex> lock(g_camera_mutex);
        g_camera_url.clear();
        g_camera_ready = false;
    }

    int rc = fn(agent, std::string(packed_dev_id), [](std::string url) {
        std::lock_guard<std::mutex> lock(g_camera_mutex);
        g_camera_url = url;
        g_camera_ready = true;
        g_camera_cv.notify_all();
    });
    if (rc != 0) {
        g_last_error = "get_camera_url call not accepted";
        return rc;
    }

    std::unique_lock<std::mutex> lock(g_camera_mutex);
    if (!g_camera_cv.wait_for(lock, std::chrono::milliseconds(timeout_ms),
                              [] { return g_camera_ready; })) {
        g_last_error = "get_camera_url timed out";
        return -2;
    }
    return 0;
}

const char* bsw_camera_url_result(void) { return g_camera_url.c_str(); }

typedef int (*set_headers_fn)(void*, std::map<std::string, std::string>);
typedef int (*user_print_info_fn)(void*, unsigned int*, std::string*);

// Studio installs this fingerprint map during cold boot; without it the cloud
// mints (ttcode) answer 403 and their callbacks never fire.
int bsw_set_headers(void* agent, const char* slicer_uuid) {
    auto fn = symbol<set_headers_fn>("bambu_network_set_extra_http_header");
    if (fn == nullptr || agent == nullptr) {
        g_last_error = "bambu_network_set_extra_http_header not found";
        return -1;
    }
    std::map<std::string, std::string> headers = {
        {"X-BBL-Client-Name", "BambuStudio"},
        {"X-BBL-Client-Type", "slicer"},
        {"X-BBL-Client-Version", "02.09.01.70"},
        {"X-BBL-Device-ID", slicer_uuid != nullptr ? slicer_uuid : ""},
        {"X-BBL-Language", "en-US"},
        {"X-BBL-OS-Type", "macos"},
        {"X-BBL-OS-Version", "27.2.1"},
        {"accept", "application/json"},
    };
    return fn(agent, headers);
}

// Populates the agent's cloud device table; camera minting needs the device.
int bsw_user_print_info(void* agent, char* body_out, int body_size) {
    auto fn = symbol<user_print_info_fn>("bambu_network_get_user_print_info");
    if (fn == nullptr || agent == nullptr) {
        g_last_error = "bambu_network_get_user_print_info not found";
        return -1;
    }
    unsigned int code = 0;
    std::string body;
    int rc = fn(agent, &code, &body);
    if (body_out != nullptr && body_size > 0) {
        snprintf(body_out, static_cast<size_t>(body_size), "%u:%s", code, body.c_str());
    }
    return rc;
}

typedef int (*set_cert_fn)(void*, std::string, std::string);
typedef int (*set_country_fn)(void*, std::string);

// Studio ships its slicer certificate next to the app; the plugin needs it for
// every cloud HTTPS call (the ttcode mint included).
int bsw_setup_cloud(void* agent, const char* cert_dir, const char* country) {
    auto cert = symbol<set_cert_fn>("bambu_network_set_cert_file");
    auto cc = symbol<set_country_fn>("bambu_network_set_country_code");
    if (cert == nullptr || cc == nullptr) {
        g_last_error = "cloud setup symbols missing";
        return -1;
    }
    cert(agent, std::string(cert_dir), std::string("slicer_base64.cer"));
    cc(agent, std::string(country));
    return 0;
}

// Cloud-mode counterpart of connect_printer: the plugin needs to know which
// machine is selected before it will mint a liveview URL for it.
int bsw_server_connected(void* agent) {
    typedef bool (*fn_t)(void*);
    auto fn = symbol<fn_t>("bambu_network_is_server_connected");
    if (fn == nullptr || agent == nullptr) return -1;
    return fn(agent) ? 1 : 0;
}

int bsw_select_machine(void* agent, const char* dev_id) {
    typedef int (*fn_t)(void*, std::string);
    auto fn = symbol<fn_t>("bambu_network_set_user_selected_machine");
    if (fn == nullptr || agent == nullptr) {
        g_last_error = "bambu_network_set_user_selected_machine not found";
        return -1;
    }
    return fn(agent, std::string(dev_id));
}

int bsw_selected_machine(void* agent, char* out, int size) {
    typedef std::string (*fn_t)(void*);
    auto fn = symbol<fn_t>("bambu_network_get_user_selected_machine");
    if (fn == nullptr || agent == nullptr) return -1;
    auto value = fn(agent);
    if (out != nullptr && size > 0) snprintf(out, static_cast<size_t>(size), "%s", value.c_str());
    return 0;
}

int bsw_connect_server(void* agent) {
    typedef int (*fn_t)(void*);
    auto fn = symbol<fn_t>("bambu_network_connect_server");
    if (fn == nullptr || agent == nullptr) return -1;
    return fn(agent);
}

int bsw_is_user_login(void* agent) {
    typedef bool (*fn_t)(void*);
    auto fn = symbol<fn_t>("bambu_network_is_user_login");
    if (fn == nullptr || agent == nullptr) return -1;
    return fn(agent) ? 1 : 0;
}

int bsw_discovery(void* agent, int start, int sending) {
    auto fn = symbol<start_discovery_fn>("bambu_network_start_discovery");
    if (fn == nullptr || agent == nullptr) {
        g_last_error = "bambu_network_start_discovery not found";
        return -1;
    }
    return fn(agent, start != 0, sending != 0) ? 0 : -1;
}

int bsw_init_log(void* agent) {
    auto fn = symbol<init_log_fn>("bambu_network_init_log");
    if (fn == nullptr || agent == nullptr) {
        return -1;
    }
    return fn(agent);
}

int bsw_start(void* agent) {
    auto fn = symbol<start_fn>("bambu_network_start");
    if (fn == nullptr || agent == nullptr) {
        g_last_error = "bambu_network_start not found";
        return -1;
    }
    return fn(agent);
}

int bsw_destroy_agent(void* agent) {
    auto fn = symbol<destroy_agent_fn>("bambu_network_destroy_agent");
    if (fn == nullptr || agent == nullptr) {
        return -1;
    }
    return fn(agent);
}

}  // extern "C"

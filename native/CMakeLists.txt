cmake_minimum_required(VERSION 3.24)
project(WallpaperVideoEngine LANGUAGES CXX)

if(NOT WIN32)
    message(FATAL_ERROR "WallpaperVideoEngine is Windows-only.")
endif()

set(WEBVIEW2_SDK_ROOT "" CACHE PATH
    "Path to the extracted Microsoft.Web.WebView2 NuGet package")
if(NOT WEBVIEW2_SDK_ROOT)
    message(FATAL_ERROR "Set WEBVIEW2_SDK_ROOT to the extracted WebView2 package.")
endif()

if(CMAKE_SIZEOF_VOID_P EQUAL 8)
    set(WEBVIEW2_ARCH x64)
else()
    set(WEBVIEW2_ARCH x86)
endif()

find_path(WEBVIEW2_INCLUDE_DIR WebView2.h
    PATHS "${WEBVIEW2_SDK_ROOT}/build/native/include"
    NO_DEFAULT_PATH REQUIRED)
find_library(WEBVIEW2_LOADER_LIBRARY NAMES WebView2LoaderStatic
    PATHS "${WEBVIEW2_SDK_ROOT}/build/native/${WEBVIEW2_ARCH}"
    NO_DEFAULT_PATH REQUIRED)

add_library(WallpaperVideoEngine SHARED src/wallpaper_video_engine.cpp)
target_compile_features(WallpaperVideoEngine PRIVATE cxx_std_20)
target_compile_definitions(WallpaperVideoEngine PRIVATE
    UNICODE _UNICODE WIN32_LEAN_AND_MEAN NOMINMAX _WIN32_WINNT=0x0A00)
target_include_directories(WallpaperVideoEngine PRIVATE "${WEBVIEW2_INCLUDE_DIR}")
target_link_libraries(WallpaperVideoEngine PRIVATE
    "${WEBVIEW2_LOADER_LIBRARY}" user32 ole32 shell32 dwmapi)

if(MSVC)
    target_compile_options(WallpaperVideoEngine PRIVATE /W4 /EHsc /permissive- /Zc:__cplusplus)
    set_property(TARGET WallpaperVideoEngine PROPERTY
        MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>DLL")
endif()

set_target_properties(WallpaperVideoEngine PROPERTIES
    OUTPUT_NAME "WallpaperVideoEngine" CXX_EXTENSIONS OFF)

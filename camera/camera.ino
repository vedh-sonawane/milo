// Freenove ESP32-WROVER CAM (GC0308 sensor, no hardware JPEG)
// "/"        -> MJPEG live stream (QVGA, JPEG quality 50)
// "/capture" -> single snapshot   (VGA,  JPEG quality 90; re-inits the camera, ~1 s)
// WebServer handles one client at a time: close the stream before calling /capture.

#include "esp_camera.h"
#include "img_converters.h"
#include <WiFi.h>
#include <WebServer.h>
#include "secrets.h"   // WIFI_SSID and WIFI_PASSWORD; copy secrets.example.h to create it

WebServer server(80);

// The camera driver fixes the expected frame size at init, and with RGB565 it
// drops every frame whose size differs ("FB-SIZE: 153600 != 614400"), so
// set_framesize() at runtime doesn't work. Changing resolution means a re-init.
bool initCamera(framesize_t size) {
  camera_config_t config = {};
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer   = LEDC_TIMER_0;
  config.pin_d0 = 4;   config.pin_d1 = 5;   config.pin_d2 = 18;  config.pin_d3 = 19;
  config.pin_d4 = 36;  config.pin_d5 = 39;  config.pin_d6 = 34;  config.pin_d7 = 35;
  config.pin_xclk = 21;  config.pin_pclk = 22;  config.pin_vsync = 25;  config.pin_href = 23;
  config.pin_sccb_sda = 26;  config.pin_sccb_scl = 27;
  config.pin_pwdn = -1;  config.pin_reset = -1;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_RGB565;   // GC0308 has no JPEG encoder
  config.frame_size   = size;
  config.fb_location  = CAMERA_FB_IN_PSRAM;
  config.fb_count     = 2;
  config.grab_mode    = CAMERA_GRAB_LATEST;

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("Camera init failed: 0x%x\n", err);
    return false;
  }
  return true;
}

bool switchFramesize(framesize_t size) {
  esp_camera_deinit();
  return initCamera(size);
}

void handleStream() {
  WiFiClient client = server.client();
  client.print("HTTP/1.1 200 OK\r\n"
               "Content-Type: multipart/x-mixed-replace; boundary=frame\r\n"
               "Cache-Control: no-cache\r\n\r\n");

  while (client.connected()) {
    camera_fb_t* fb = esp_camera_fb_get();
    if (!fb) { Serial.println("Stream: frame grab failed"); break; }

    uint8_t* jpg = nullptr;
    size_t jpgLen = 0;
    bool ok = frame2jpg(fb, 50, &jpg, &jpgLen);
    esp_camera_fb_return(fb);
    if (!ok) { Serial.println("Stream: JPEG conversion failed"); break; }

    client.printf("--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n", jpgLen);
    client.write(jpg, jpgLen);
    client.print("\r\n");
    free(jpg);
  }
}

void handleCapture() {
  if (!switchFramesize(FRAMESIZE_VGA)) {
    switchFramesize(FRAMESIZE_QVGA);
    server.send(500, "text/plain", "Camera re-init at VGA failed");
    return;
  }

  // Discard frames while exposure/white balance settle.
  for (int i = 0; i < 3; i++) {
    camera_fb_t* stale = esp_camera_fb_get();
    if (stale) esp_camera_fb_return(stale);
  }

  camera_fb_t* fb = esp_camera_fb_get();
  if (!fb) {
    switchFramesize(FRAMESIZE_QVGA);
    server.send(500, "text/plain", "Frame grab failed");
    return;
  }

  uint8_t* jpg = nullptr;
  size_t jpgLen = 0;
  bool ok = frame2jpg(fb, 90, &jpg, &jpgLen);
  esp_camera_fb_return(fb);
  switchFramesize(FRAMESIZE_QVGA);

  if (!ok) {
    server.send(500, "text/plain", "JPEG conversion failed");
    return;
  }

  server.setContentLength(jpgLen);
  server.send(200, "image/jpeg", "");
  server.sendContent((const char*)jpg, jpgLen);
  free(jpg);
}

void setup() {
  Serial.begin(115200);
  Serial.println();

  if (!initCamera(FRAMESIZE_QVGA)) {
    while (true) delay(1000);
  }

  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("Connecting to Wi-Fi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();

  server.on("/", handleStream);
  server.on("/capture", handleCapture);
  server.begin();

  Serial.print("Stream:  http://");  Serial.print(WiFi.localIP());  Serial.println("/");
  Serial.print("Capture: http://");  Serial.print(WiFi.localIP());  Serial.println("/capture");
}

void loop() {
  server.handleClient();
}

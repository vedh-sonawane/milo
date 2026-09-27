// Freenove ESP32-WROVER CAM (GC0308 sensor, no hardware JPEG)
// "/"        -> MJPEG live stream (VGA, JPEG quality 50, a few fps; for aiming only)
// "/capture" -> single snapshot   (VGA, JPEG quality 100, ~1 s; ?q=1-100 to override)
// WebServer handles one client at a time: close the stream before calling /capture.

#include "esp_camera.h"
#include "img_converters.h"
#include <WiFi.h>
#include <WebServer.h>
#include "secrets.h"   // WIFI_SSID and WIFI_PASSWORD; copy secrets.example.h to create it

WebServer server(80);

// Everything runs at VGA. The camera driver fixes the frame size at init and, with
// RGB565, drops every frame of a different size ("FB-SIZE: 153600 != 614400"), so
// switching resolution needs a slow (~3 s) and flaky re-init. Fast captures matter
// more than a smooth preview stream for a device worn while walking.
bool initCamera() {
  camera_config_t config = {};
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer   = LEDC_TIMER_0;
  config.pin_d0 = 4;   config.pin_d1 = 5;   config.pin_d2 = 18;  config.pin_d3 = 19;
  config.pin_d4 = 36;  config.pin_d5 = 39;  config.pin_d6 = 34;  config.pin_d7 = 35;
  config.pin_xclk = 21;  config.pin_pclk = 22;  config.pin_vsync = 25;  config.pin_href = 23;
  config.pin_sccb_sda = 26;  config.pin_sccb_scl = 27;
  config.pin_pwdn = -1;  config.pin_reset = -1;
  // 10 MHz, not 20: at 20 MHz the RGB565 VGA data outruns the DMA into PSRAM and frames are silently
  // dropped (a grab could wait 0-3 s). At 10 MHz frames arrive steadily every 200 ms (5 fps).
  config.xclk_freq_hz = 10000000;
  config.pixel_format = PIXFORMAT_RGB565;   // GC0308 has no JPEG encoder
  config.frame_size   = FRAMESIZE_VGA;
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
  // JPEG quality 1-100; default 100 (maximum). Costs ~0.2 s over 90. ?q= overrides it.
  int quality = server.hasArg("q") ? constrain(server.arg("q").toInt(), 1, 100) : 100;

  unsigned long t0 = millis();
  camera_fb_t* fb = esp_camera_fb_get();   // CAMERA_GRAB_LATEST already gives the newest frame
  if (!fb) {
    server.send(500, "text/plain", "Frame grab failed");
    return;
  }
  unsigned long t1 = millis();

  uint8_t* jpg = nullptr;
  size_t jpgLen = 0;
  bool ok = frame2jpg(fb, quality, &jpg, &jpgLen);
  esp_camera_fb_return(fb);
  unsigned long t2 = millis();

  if (!ok) {
    server.send(500, "text/plain", "JPEG conversion failed");
    return;
  }

  // Board-side time, so the PC can tell it apart from Wi-Fi time.
  server.sendHeader("X-Timing", String("grab=") + (t1 - t0) + "ms encode=" + (t2 - t1) + "ms");
  server.setContentLength(jpgLen);
  server.send(200, "image/jpeg", "");
  server.sendContent((const char*)jpg, jpgLen);
  free(jpg);
}

void setup() {
  Serial.begin(115200);
  Serial.println();

  if (!initCamera()) {
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
  Serial.printf("Wi-Fi signal: %d dBm\n", WiFi.RSSI());
}

void loop() {
  server.handleClient();
}

#pragma once

// Animated robot face for the StackChan 320x240 ILI9342C display.
//
// The module is split in two halves on purpose:
//  - FaceRaster is a pure, deterministic, host-testable raster model: given a
//    pixel, the lifecycle expression ("IDLE", "SLEEPING", "PAUSED", "FAULT",
//    "EMERGENCY_STOP", ...) and a millisecond clock it returns the RGB565
//    color of that pixel. No hardware dependencies, no state.
//  - SmileyFace streams the raster to a FaceWriter (implemented by
//    StackChanDisplay over SPI) and only re-paints the animated eye/mouth
//    region every kAnimationIntervalMs.
//
// The default expression is intentionally minimal: a flat background with
// cyan circular eyes and a bright animated mouth. Sleep/long blink uses the
// white half-moon eyes from the second reference.
//
// Animation (all driven by now_ms, integer-only, deterministic):
//  - the eye pair looks left/right/up/down at a short, visible cycle;
//  - the cyan eyes blink through a short squash transition;
//  - the mouth opens and closes quickly with a large visible amplitude.
//
// Pixel bytes are emitted RGB565 MSB-first (ILx934x panel order), which is
// what the SPI bus delivers after the MADCTL BGR init sequence.

#include <array>
#include <cstddef>
#include <cstdint>
#include <string_view>

namespace lifeos::hal::stackchan {

struct Rgb {
  std::uint8_t r;
  std::uint8_t g;
  std::uint8_t b;
};

constexpr std::uint16_t rgb565(Rgb color) {
  return static_cast<std::uint16_t>(((color.r >> 3) << 11) |
                                    ((color.g >> 2) << 5) | (color.b >> 3));
}

class FaceRaster {
 public:
  static constexpr int kWidth = 320;
  static constexpr int kHeight = 240;

  enum class Mood { Happy, Sleep, Paused, Fault };

  // -- Palette -------------------------------------------------------------
  static constexpr Rgb kBackgroundHappy{8, 12, 18};
  static constexpr Rgb kBackgroundSleep{4, 7, 12};
  static constexpr Rgb kBackgroundPaused{25, 21, 14};
  static constexpr Rgb kBackgroundFault{38, 10, 16};
  static constexpr Rgb kEyeColor{80, 218, 239};
  static constexpr Rgb kEyeGlowColor{7, 79, 136};
  static constexpr Rgb kEyeShadowColor{14, 33, 132};
  static constexpr Rgb kEyeHighlightColor{194, 247, 255};
  static constexpr Rgb kClosedEyeColor{238, 239, 235};
  static constexpr Rgb kPausedEyeColor{246, 185, 76};
  static constexpr Rgb kFaultEyeColor{244, 91, 95};
  static constexpr Rgb kMouthColor{238, 129, 198};

  // -- Geometry -------------------------------------------------------------
  static constexpr int kFaceCx = 160;
  static constexpr int kEyeY = 110;
  static constexpr int kEyeLeftX = 101;
  static constexpr int kEyeRightX = 219;
  static constexpr int kEyeRx = 28;
  static constexpr int kEyeRy = 28;
  static constexpr int kEyeGlowRadius = 5;
  static constexpr int kGazeAmplitude = 6;
  static constexpr int kGazeVerticalAmplitude = 3;
  static constexpr int kMouthY = 181;
  static constexpr int kMouthHalfW = 34;
  static constexpr int kMouthMinHalfH = 2;
  static constexpr int kMouthMaxHalfH = 12;

  // -- Animation timing ----------------------------------------------------
  static constexpr std::uint64_t kBlinkCycleMs = 3600;
  static constexpr std::uint64_t kBlinkStartMs = 3000;
  static constexpr std::uint64_t kBlinkDurationMs = 260;
  static constexpr std::uint64_t kMouthCycleMs = 360;
  static constexpr std::uint64_t kGazeCycleMs = 1200;
  static constexpr std::uint64_t kGazeVerticalCycleMs = 900;

  static Mood mood(std::string_view expression);
  static Rgb background(Mood mood);
  // Animated expression is redrawn periodically; sleep/fault faces are static.
  static bool animated(Mood mood);
  static int eye_gaze_x(std::uint64_t now_ms);
  static int eye_gaze_y(std::uint64_t now_ms);
  static int blink_height(std::uint64_t now_ms);
  static bool blink_closed(std::uint64_t now_ms);
  // Vertical mouth opening in pixels. The short cycle keeps speech/activity
  // visually immediate at the 50 ms display refresh cadence.
  static int mouth_opening(std::uint64_t now_ms);

  // RGB565 color of pixel (x, y) at time now_ms for the given mood. Pure and
  // deterministic; out-of-range pixels return the background color.
  std::uint16_t pixel(int x, int y, Mood mood, std::uint64_t now_ms) const;

  std::uint16_t pixel(int x, int y, std::string_view expression,
                      std::uint64_t now_ms) const {
    return pixel(x, y, mood(expression), now_ms);
  }

 private:
  static bool inside_ellipse(int dx, int dy, int rx, int ry);
  static bool inside_circle(int dx, int dy, int radius);
  static bool closed_eye(int x, int y, int eye_x, int eye_y);
  static bool open_eye(int x, int y, int eye_x, int eye_y, int radius_y);
  static bool cross_eye(int x, int y, int eye_x, int eye_y);
  static bool mouth(int x, int y, Mood mood, std::uint64_t now_ms);
};

// Low-level paint surface; implemented by StackChanDisplay over SPI.
class FaceWriter {
 public:
  virtual ~FaceWriter() = default;
  // Open a window (inclusive coordinates) for the following write_row calls.
  virtual bool begin_region(int x0, int y0, int x1, int y1) = 0;
  // One full row of RGB565 pixels, MSB-first, exactly (x1 - x0 + 1) * 2 bytes.
  virtual bool write_row(const std::uint8_t* rgb565_msb_first,
                         std::size_t byte_count) = 0;
};

// Bounding box (inclusive) of the animated eye/mouth region.
struct FaceRect {
  int x0;
  int y0;
  int x1;
  int y1;
};

class SmileyFace {
 public:
  static constexpr std::uint64_t kAnimationIntervalMs = 50;

  explicit SmileyFace(FaceWriter& writer) : writer_(writer) {}

  // Paints (or repaints) the face for the current expression/clock. The first
  // call and every expression change repaint the whole screen; otherwise only
  // the animated eye/mouth region is refreshed at kAnimationIntervalMs.
  bool draw(std::string_view expression, std::uint64_t now_ms);

  bool full_drawn() const { return full_drawn_; }

 private:
  bool paint_region(int x0, int y0, int x1, int y1, FaceRaster::Mood mood,
                    std::uint64_t now_ms);

  FaceWriter& writer_;
  FaceRaster raster_;
  std::array<char, 16> last_expression_{};
  std::uint64_t last_animation_ms_{0};
  bool full_drawn_{false};
};

// Animated eye/mouth region; also used by tests.
constexpr FaceRect kFaceRegion{
    FaceRaster::kEyeLeftX - FaceRaster::kEyeRx - FaceRaster::kEyeGlowRadius -
        FaceRaster::kGazeAmplitude - 1,
    FaceRaster::kEyeY - FaceRaster::kEyeRy - FaceRaster::kEyeGlowRadius -
        FaceRaster::kGazeVerticalAmplitude - 1,
    FaceRaster::kEyeRightX + FaceRaster::kEyeRx + FaceRaster::kEyeGlowRadius +
        FaceRaster::kGazeAmplitude + 1,
    FaceRaster::kMouthY + FaceRaster::kMouthMaxHalfH + 1,
};

}  // namespace lifeos::hal::stackchan

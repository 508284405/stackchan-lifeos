#include "lifeos/hal/stackchan/face.hpp"

#include <cassert>
#include <cstdint>
#include <cstddef>
#include <vector>

using namespace lifeos::hal::stackchan;

namespace {

std::uint16_t happy_bg() { return rgb565(FaceRaster::kBackgroundHappy); }
std::uint16_t eye() { return rgb565(FaceRaster::kEyeColor); }
std::uint16_t glow() { return rgb565(FaceRaster::kEyeGlowColor); }
std::uint16_t shadow() { return rgb565(FaceRaster::kEyeShadowColor); }
std::uint16_t highlight() { return rgb565(FaceRaster::kEyeHighlightColor); }
std::uint16_t closed_eye() { return rgb565(FaceRaster::kClosedEyeColor); }
std::uint16_t paused_eye() { return rgb565(FaceRaster::kPausedEyeColor); }
std::uint16_t fault_eye() { return rgb565(FaceRaster::kFaultEyeColor); }
std::uint16_t mouth() { return rgb565(FaceRaster::kMouthColor); }

struct MockWriter final : FaceWriter {
  struct Region {
    int x0;
    int y0;
    int x1;
    int y1;
    std::size_t bytes;
    int rows;
  };
  std::vector<Region> regions;
  std::vector<std::uint8_t> all_bytes;

  bool begin_region(int x0, int y0, int x1, int y1) override {
    regions.push_back({x0, y0, x1, y1, 0, 0});
    return true;
  }
  bool write_row(const std::uint8_t* rgb565_msb_first,
                 std::size_t byte_count) override {
    assert(!regions.empty());
    Region& region = regions.back();
    region.bytes += byte_count;
    region.rows += 1;
    all_bytes.insert(all_bytes.end(), rgb565_msb_first,
                     rgb565_msb_first + byte_count);
    return true;
  }
};

}  // namespace

int main() {
  FaceRaster raster;
  const std::uint64_t t_open = 100;
  const std::uint64_t t_blink = FaceRaster::kBlinkStartMs +
                                FaceRaster::kBlinkDurationMs / 2;

  // -- Mood mapping --------------------------------------------------------
  assert(FaceRaster::mood("IDLE") == FaceRaster::Mood::Happy);
  assert(FaceRaster::mood("ACTING") == FaceRaster::Mood::Happy);
  assert(FaceRaster::mood("BOOT") == FaceRaster::Mood::Happy);
  assert(FaceRaster::mood("") == FaceRaster::Mood::Happy);
  assert(FaceRaster::mood("DEGRADED") == FaceRaster::Mood::Happy);
  assert(FaceRaster::mood("SLEEPING") == FaceRaster::Mood::Sleep);
  assert(FaceRaster::mood("PAUSED") == FaceRaster::Mood::Paused);
  assert(FaceRaster::mood("FAULT") == FaceRaster::Mood::Fault);
  assert(FaceRaster::mood("EMERGENCY_STOP") == FaceRaster::Mood::Fault);
  assert(FaceRaster::mood("unexpected-mode") == FaceRaster::Mood::Happy);

  // -- Backgrounds ---------------------------------------------------------
  assert(raster.pixel(5, 5, "IDLE", 0) == happy_bg());
  assert(raster.pixel(5, 5, "SLEEPING", 0) ==
         rgb565(FaceRaster::kBackgroundSleep));
  assert(raster.pixel(5, 5, "PAUSED", 0) ==
         rgb565(FaceRaster::kBackgroundPaused));
  assert(raster.pixel(5, 5, "FAULT", 0) ==
         rgb565(FaceRaster::kBackgroundFault));
  // Out-of-range pixels are safe.
  assert(raster.pixel(-1, -1, "IDLE", 0) == happy_bg());
  assert(raster.pixel(320, 240, "FAULT", 0) ==
         rgb565(FaceRaster::kBackgroundFault));

  // -- Minimal face: no surrounding panel or status bar -------------------
  assert(raster.pixel(160, 40, "IDLE", t_open) == happy_bg());
  assert(raster.pixel(30, 120, "IDLE", t_open) == happy_bg());
  assert(raster.pixel(160, 160, "IDLE", t_open) == happy_bg());
  assert(raster.pixel(160, 230, "IDLE", t_open) == happy_bg());

  // -- Default eyes: cyan discs with blue shadow/glow and a moving highlight.
  const int open_x = FaceRaster::kEyeLeftX + FaceRaster::eye_gaze_x(t_open);
  const int open_y = FaceRaster::kEyeY + FaceRaster::eye_gaze_y(t_open);
  assert(raster.pixel(open_x, open_y, "IDLE", t_open) == eye());
  assert(raster.pixel(open_x - 9, open_y - 9, "IDLE", t_open) == highlight());
  assert(raster.pixel(open_x - FaceRaster::kEyeRx - FaceRaster::kEyeGlowRadius,
                      open_y, "IDLE", t_open) == glow());
  assert(raster.pixel(open_x + FaceRaster::kEyeRx + 1, open_y + 4,
                      "IDLE", t_open) == shadow());
  assert(FaceRaster::eye_gaze_x(0) == -FaceRaster::kGazeAmplitude);
  assert(FaceRaster::eye_gaze_x(FaceRaster::kGazeCycleMs / 2) ==
         FaceRaster::kGazeAmplitude);

  // -- Mouth: large, fast open/close motion -------------------------------
  assert(FaceRaster::mouth_opening(0) == FaceRaster::kMouthMinHalfH);
  assert(FaceRaster::mouth_opening(FaceRaster::kMouthCycleMs / 2) ==
         FaceRaster::kMouthMaxHalfH);
  assert(raster.pixel(FaceRaster::kFaceCx, FaceRaster::kMouthY, "IDLE",
                      FaceRaster::kMouthCycleMs / 2) == mouth());
  assert(raster.pixel(FaceRaster::kFaceCx,
                      FaceRaster::kMouthY - FaceRaster::kMouthMaxHalfH - 1,
                      "IDLE", FaceRaster::kMouthCycleMs / 2) == happy_bg());

  // -- Blink: squash to a slit, then show the white half-moon eye.
  assert(FaceRaster::blink_height(t_open) == FaceRaster::kEyeRy);
  assert(FaceRaster::blink_height(t_blink) == 2);
  const int blink_x = FaceRaster::kEyeLeftX + FaceRaster::eye_gaze_x(t_blink);
  const int blink_y = FaceRaster::kEyeY + FaceRaster::eye_gaze_y(t_blink);
  assert(FaceRaster::blink_closed(t_blink));
  assert(raster.pixel(blink_x, blink_y - 10, "IDLE", t_blink) == closed_eye());

  // -- Sleep: static white half-moon eyes from the second reference robot.
  const int sleep_x = FaceRaster::kEyeLeftX;
  const int sleep_y = FaceRaster::kEyeY;
  assert(raster.pixel(sleep_x, sleep_y - 10, "SLEEPING", 0) == closed_eye());
  assert(raster.pixel(FaceRaster::kFaceCx, FaceRaster::kMouthY, "SLEEPING",
                      0) == glow());

  // -- Paused: amber eyes and an animated mouth.
  const int paused_x = FaceRaster::kEyeLeftX + FaceRaster::eye_gaze_x(0);
  const int paused_y = FaceRaster::kEyeY + FaceRaster::eye_gaze_y(0);
  assert(raster.pixel(paused_x, paused_y, "PAUSED", 0) == paused_eye());
  assert(FaceRaster::animated(FaceRaster::Mood::Paused));

  // -- Fault: explicit red X eyes and red mouth.
  const int fault_x = FaceRaster::kEyeLeftX;
  const int fault_y = FaceRaster::kEyeY;
  assert(raster.pixel(fault_x, fault_y, "FAULT", 0) == fault_eye());
  assert(raster.pixel(FaceRaster::kFaceCx, FaceRaster::kMouthY, "FAULT", 0) ==
         fault_eye());
  assert(!FaceRaster::animated(FaceRaster::Mood::Fault));

  // -- Determinism ----------------------------------------------------------
  assert(raster.pixel(100, 80, "IDLE", 700) ==
         raster.pixel(100, 80, "IDLE", 700));

  // -- SmileyFace paint scheduling -------------------------------------------
  {
    MockWriter writer;
    SmileyFace smile(writer);

    // First draw: full screen, RGB565 MSB-first rows.
    assert(smile.draw("IDLE", 0));
    assert(writer.regions.size() == 1);
    assert(writer.regions[0].x0 == 0 && writer.regions[0].y0 == 0);
    assert(writer.regions[0].x1 == FaceRaster::kWidth - 1);
    assert(writer.regions[0].y1 == FaceRaster::kHeight - 1);
    assert(writer.regions[0].rows == FaceRaster::kHeight);
    assert(writer.regions[0].bytes ==
           static_cast<std::size_t>(FaceRaster::kWidth) * 2 *
               FaceRaster::kHeight);
    // Byte order: MSB first, so pixel (0,0) starts with the high byte.
    assert(writer.all_bytes.size() == writer.regions[0].bytes);
    assert(writer.all_bytes[0] == static_cast<std::uint8_t>(happy_bg() >> 8));
    assert(writer.all_bytes[1] == static_cast<std::uint8_t>(happy_bg() & 0xFF));

    // Same expression, animation due: only the animated eye/mouth region.
    writer.all_bytes.clear();
    assert(smile.draw("IDLE", 50));
    assert(writer.regions.size() == 2);
    assert(writer.regions[1].x0 == kFaceRegion.x0 &&
           writer.regions[1].y0 == kFaceRegion.y0 &&
           writer.regions[1].x1 == kFaceRegion.x1 &&
           writer.regions[1].y1 == kFaceRegion.y1);
    const int width = kFaceRegion.x1 - kFaceRegion.x0 + 1;
    const int height = kFaceRegion.y1 - kFaceRegion.y0 + 1;
    assert(writer.regions[1].bytes ==
           static_cast<std::size_t>(width) * 2 * height);

    // Too soon: no repaint.
    const std::size_t before = writer.regions.size();
    assert(smile.draw("IDLE", 80));
    assert(writer.regions.size() == before);

    // Expression change: full screen again.
    assert(smile.draw("SLEEPING", 200));
    assert(writer.regions.size() == before + 1);
    assert(writer.regions.back().x0 == 0 && writer.regions.back().y1 ==
                                               FaceRaster::kHeight - 1);

    // Static mood: no periodic repaint.
    const std::size_t before_static = writer.regions.size();
    assert(smile.draw("SLEEPING", 300));
    assert(writer.regions.size() == before_static);

    // Paused is animated: expression change repaints the full screen, then
    // periodic ticks repaint the eye/mouth region.
    assert(smile.draw("PAUSED", 400));
    assert(writer.regions.size() == before_static + 1);
    assert(writer.regions.back().x0 == 0 &&
           writer.regions.back().y1 == FaceRaster::kHeight - 1);
    assert(smile.draw("PAUSED", 450));
    assert(writer.regions.size() == before_static + 2);
    assert(writer.regions.back().x0 == kFaceRegion.x0);
  }

  return 0;
}

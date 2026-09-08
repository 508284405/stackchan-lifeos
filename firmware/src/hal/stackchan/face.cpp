#include "lifeos/hal/stackchan/face.hpp"

#include <algorithm>
#include <cstdlib>
#include <cstring>

namespace lifeos::hal::stackchan {

FaceRaster::Mood FaceRaster::mood(std::string_view expression) {
  if (expression == "FAULT" || expression == "EMERGENCY_STOP") return Mood::Fault;
  if (expression == "SLEEPING") return Mood::Sleep;
  if (expression == "PAUSED") return Mood::Paused;
  return Mood::Happy;
}

Rgb FaceRaster::background(Mood mood) {
  switch (mood) {
    case Mood::Sleep:
      return kBackgroundSleep;
    case Mood::Paused:
      return kBackgroundPaused;
    case Mood::Fault:
      return kBackgroundFault;
    default:
      return kBackgroundHappy;
  }
}

bool FaceRaster::animated(Mood mood) {
  return mood == Mood::Happy || mood == Mood::Paused;
}

namespace {

// Integer triangle wave in [-amplitude, +amplitude] over cycle_ms. No float
// math anywhere in the raster, so host tests and the device agree bit-exactly.
int triangle(std::uint64_t now_ms, std::uint64_t cycle_ms, int amplitude) {
  const std::uint64_t phase = now_ms % cycle_ms;
  const std::uint64_t half = cycle_ms / 2;
  if (phase < half) {
    return -amplitude +
           static_cast<int>((2 * phase * static_cast<std::uint64_t>(amplitude)) / half);
  }
  return amplitude -
         static_cast<int>((2 * (phase - half) * static_cast<std::uint64_t>(amplitude)) / half);
}

}  // namespace

bool FaceRaster::inside_ellipse(int dx, int dy, int rx, int ry) {
  const std::int64_t a = dx;
  const std::int64_t b = dy;
  return a * a * ry * ry + b * b * rx * rx <=
         static_cast<std::int64_t>(rx) * rx * ry * ry;
}

bool FaceRaster::inside_circle(int dx, int dy, int radius) {
  return dx * dx + dy * dy <= radius * radius;
}

int FaceRaster::eye_gaze_x(std::uint64_t now_ms) {
  return triangle(now_ms, kGazeCycleMs, kGazeAmplitude);
}

int FaceRaster::eye_gaze_y(std::uint64_t now_ms) {
  return triangle(now_ms, kGazeVerticalCycleMs, kGazeVerticalAmplitude);
}

int FaceRaster::mouth_opening(std::uint64_t now_ms) {
  const std::uint64_t phase = now_ms % kMouthCycleMs;
  const std::uint64_t half = kMouthCycleMs / 2;
  const int span = kMouthMaxHalfH - kMouthMinHalfH;
  if (phase < half) {
    return kMouthMinHalfH +
           static_cast<int>((span * phase) / half);
  }
  return kMouthMaxHalfH -
         static_cast<int>((span * (phase - half)) / half);
}

int FaceRaster::blink_height(std::uint64_t now_ms) {
  const std::uint64_t phase = now_ms % kBlinkCycleMs;
  if (phase < kBlinkStartMs || phase >= kBlinkStartMs + kBlinkDurationMs) {
    return kEyeRy;
  }
  constexpr int kClosedHeight = 2;
  const std::uint64_t elapsed = phase - kBlinkStartMs;
  const std::uint64_t third = kBlinkDurationMs / 3;
  if (elapsed < third) {
    return kEyeRy - static_cast<int>((kEyeRy - kClosedHeight) * elapsed / third);
  }
  if (elapsed < 2 * third) return kClosedHeight;
  return kClosedHeight + static_cast<int>(
      (kEyeRy - kClosedHeight) * (elapsed - 2 * third) / third);
}

bool FaceRaster::blink_closed(std::uint64_t now_ms) {
  const std::uint64_t phase = now_ms % kBlinkCycleMs;
  if (phase < kBlinkStartMs || phase >= kBlinkStartMs + kBlinkDurationMs) return false;
  const std::uint64_t elapsed = phase - kBlinkStartMs;
  return elapsed >= kBlinkDurationMs / 3 && elapsed < 2 * kBlinkDurationMs / 3;
}

bool FaceRaster::closed_eye(int x, int y, int eye_x, int eye_y) {
  const int dx = x - eye_x;
  const int dy = y - eye_y;
  // Upper half of an ellipse with a flat lower edge, matching the relaxed
  // white half-moon eyes in the second reference robot.
  return dy <= 0 && inside_ellipse(dx, dy, kEyeRx, kEyeRy - 5);
}

bool FaceRaster::open_eye(int x, int y, int eye_x, int eye_y, int radius_y) {
  const int dx = x - eye_x;
  const int dy = y - eye_y;
  return inside_ellipse(dx, dy, kEyeRx, std::max(1, radius_y));
}

bool FaceRaster::cross_eye(int x, int y, int eye_x, int eye_y) {
  const int dx = x - eye_x;
  const int dy = y - eye_y;
  if (std::abs(dx) > 13 || std::abs(dy) > 13) return false;
  return std::abs(dx - dy) <= 2 || std::abs(dx + dy) <= 2;
}

bool FaceRaster::mouth(int x, int y, Mood mood, std::uint64_t now_ms) {
  const int opening = animated(mood) ? mouth_opening(now_ms) : kMouthMinHalfH;
  return inside_ellipse(x - kFaceCx, y - kMouthY, kMouthHalfW, opening);
}

std::uint16_t FaceRaster::pixel(int x, int y, Mood mood,
                                std::uint64_t now_ms) const {
  if (x < 0 || x >= kWidth || y < 0 || y >= kHeight) {
    return rgb565(background(mood));
  }
  const int gaze_x = animated(mood) ? eye_gaze_x(now_ms) : 0;
  const int gaze_y = animated(mood) ? eye_gaze_y(now_ms) : 0;
  const int eye_y = kEyeY + gaze_y;

  auto paint_eye = [&](int eye_x, Rgb& color) {
    const int dx = x - (eye_x + gaze_x);
    const int dy = y - eye_y;
    if (mood == Mood::Fault) {
      if (!cross_eye(x, y, eye_x + gaze_x, eye_y)) return false;
      color = kFaultEyeColor;
      return true;
    }

    const bool closed = mood == Mood::Sleep || blink_closed(now_ms);
    if (closed) {
      if (!closed_eye(x, y, eye_x + gaze_x, eye_y)) return false;
      color = mood == Mood::Paused ? kPausedEyeColor : kClosedEyeColor;
      return true;
    }

    const int height = blink_height(now_ms);
    if (open_eye(x, y, eye_x + gaze_x, eye_y, height)) {
      const bool highlight = inside_circle(dx + 9, dy + 9, 3);
      color = highlight ? kEyeHighlightColor
                        : (mood == Mood::Paused ? kPausedEyeColor : kEyeColor);
      return true;
    }
    if (inside_circle(dx - 4, dy - 4, kEyeRx + 4)) {
      color = kEyeShadowColor;
      return true;
    }
    if (inside_circle(dx, dy, kEyeRx + kEyeGlowRadius)) {
      color = kEyeGlowColor;
      return true;
    }
    return false;
  };

  Rgb color{};
  if (paint_eye(kEyeLeftX, color) || paint_eye(kEyeRightX, color)) {
    return rgb565(color);
  }

  if (mouth(x, y, mood, now_ms)) {
    if (mood == Mood::Fault) return rgb565(kFaultEyeColor);
    if (mood == Mood::Paused) return rgb565(kPausedEyeColor);
    if (mood == Mood::Sleep) return rgb565(kEyeGlowColor);
    return rgb565(kMouthColor);
  }
  return rgb565(background(mood));
}

bool SmileyFace::draw(std::string_view expression, std::uint64_t now_ms) {
  const auto mood = FaceRaster::mood(expression);
  const bool changed = expression != std::string_view(last_expression_.data());
  if (!full_drawn_ || changed) {
    if (!paint_region(0, 0, FaceRaster::kWidth - 1, FaceRaster::kHeight - 1,
                      mood, now_ms)) {
      return false;
    }
    const std::size_t count = std::min(expression.size(), last_expression_.size() - 1);
    std::memcpy(last_expression_.data(), expression.data(), count);
    last_expression_[count] = '\0';
    full_drawn_ = true;
    last_animation_ms_ = now_ms;
    return true;
  }
  if (now_ms < last_animation_ms_ + kAnimationIntervalMs) return true;
  last_animation_ms_ = now_ms;
  if (!FaceRaster::animated(mood)) return true;
  return paint_region(kFaceRegion.x0, kFaceRegion.y0, kFaceRegion.x1, kFaceRegion.y1,
                      mood, now_ms);
}

bool SmileyFace::paint_region(int x0, int y0, int x1, int y1,
                              FaceRaster::Mood mood, std::uint64_t now_ms) {
  x0 = std::max(0, x0);
  y0 = std::max(0, y0);
  x1 = std::min(FaceRaster::kWidth - 1, x1);
  y1 = std::min(FaceRaster::kHeight - 1, y1);
  if (x1 < x0 || y1 < y0) return true;

  const std::size_t row_bytes = static_cast<std::size_t>(x1 - x0 + 1) * 2;
  std::array<std::uint8_t, 2 * FaceRaster::kWidth> row{};
  if (!writer_.begin_region(x0, y0, x1, y1)) return false;
  for (int y = y0; y <= y1; ++y) {
    for (int x = x0; x <= x1; ++x) {
      const std::uint16_t color = raster_.pixel(x, y, mood, now_ms);
      const std::size_t offset = static_cast<std::size_t>(x - x0) * 2;
      row[offset] = static_cast<std::uint8_t>(color >> 8);
      row[offset + 1] = static_cast<std::uint8_t>(color & 0xFF);
    }
    if (!writer_.write_row(row.data(), row_bytes)) return false;
  }
  return true;
}

}  // namespace lifeos::hal::stackchan

// Dev-only preview renderer for the StackChan smiley face.
//
// Renders FaceRaster frames to PPM (P6, RGB888) so the design can be
// inspected on a host without the device:
//
//   c++ -std=c++17 -O2 -Ifirmware/include tools/render_face_preview.cpp \
//       firmware/src/hal/stackchan/face.cpp -o /tmp/render_face_preview
//   /tmp/render_face_preview artifacts/faces IDLE 0 3000 250
//   python3 tools/ppm_to_png.py artifacts/faces
//
// Output: <dir>/<expr>_t<now_ms>.ppm next to .png files.

#include "lifeos/hal/stackchan/face.hpp"

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <vector>

namespace {

std::uint8_t red(std::uint16_t color) {
  const std::uint8_t r5 = static_cast<std::uint8_t>((color >> 11) & 0x1F);
  return static_cast<std::uint8_t>((r5 << 3) | (r5 >> 2));
}
std::uint8_t green(std::uint16_t color) {
  const std::uint8_t g6 = static_cast<std::uint8_t>((color >> 5) & 0x3F);
  return static_cast<std::uint8_t>((g6 << 2) | (g6 >> 4));
}
std::uint8_t blue(std::uint16_t color) {
  const std::uint8_t b5 = static_cast<std::uint8_t>(color & 0x1F);
  return static_cast<std::uint8_t>((b5 << 3) | (b5 >> 2));
}

}  // namespace

int main(int argc, char** argv) {
  if (argc != 6) {
    std::fprintf(stderr,
                 "usage: %s <out_dir> <expression> <t_start_ms> <t_end_ms> <step_ms>\n",
                 argv[0]);
    return 1;
  }
  const char* out_dir = argv[1];
  const std::string expression = argv[2];
  const auto t_start = std::strtoull(argv[3], nullptr, 10);
  const auto t_end = std::strtoull(argv[4], nullptr, 10);
  const auto step = std::strtoull(argv[5], nullptr, 10);
  if (step == 0) return 1;

  lifeos::hal::stackchan::FaceRaster raster;
  const int width = lifeos::hal::stackchan::FaceRaster::kWidth;
  const int height = lifeos::hal::stackchan::FaceRaster::kHeight;

  for (std::uint64_t now = t_start; now <= t_end; now += step) {
    char path[512]{};
    std::snprintf(path, sizeof(path), "%s/%s_t%06llu.ppm", out_dir,
                  expression.c_str(), static_cast<unsigned long long>(now));
    std::FILE* file = std::fopen(path, "wb");
    if (file == nullptr) {
      std::perror(path);
      return 1;
    }
    std::fprintf(file, "P6\n%d %d\n255\n", width, height);
    std::vector<std::uint8_t> row(static_cast<std::size_t>(width) * 3);
    for (int y = 0; y < height; ++y) {
      for (int x = 0; x < width; ++x) {
        const std::uint16_t color = raster.pixel(x, y, expression, now);
        row[static_cast<std::size_t>(x) * 3] = red(color);
        row[static_cast<std::size_t>(x) * 3 + 1] = green(color);
        row[static_cast<std::size_t>(x) * 3 + 2] = blue(color);
      }
      std::fwrite(row.data(), 1, row.size(), file);
    }
    std::fclose(file);
  }
  return 0;
}

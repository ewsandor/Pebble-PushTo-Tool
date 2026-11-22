#include "main_window.h"

#include "../logging.h"
#include "../services/l10n.h"

#include <stdio.h>
#include <string.h>

#define DEG "\xc2\xb0"

#if defined(PBL_PLATFORM_EMERY) || defined(PBL_PLATFORM_GABBRO)
#define LARGE_COORDINATE_LAYOUT
#endif

// Gothic 18 ink starts 7 px below the text origin. Allow for descenders
// in the footer, but center the clock/temperature on the digit height.
#define GOTHIC_18_INK_TOP 7
#define STATUS_TEXT_INK_H 11
#define FOOTER_TEXT_INK_H 14

#if defined(PBL_PLATFORM_EMERY)
#define CAPTION_INK_TOP GOTHIC_18_INK_TOP
#define CAPTION_INK_H 11
#else
#define CAPTION_INK_TOP 5
#define CAPTION_INK_H 9
#endif

typedef struct {
  int16_t status_h, label_y, label_h, label_ink_top, label_ink_bottom, footer_y;
  const char *status_font, *label_font, *caption_font, *footer_font;
  bool round;
} Layout;

typedef struct {
  const char *key;
  uint8_t ink_top, ink_h;
} CoordinateFont;

// Digit ink relative to the Pebble text origin, in descending size order.
static const CoordinateFont s_coordinate_fonts[] = {
#ifdef LARGE_COORDINATE_LAYOUT
  {FONT_KEY_LECO_60_NUMBERS_AM_PM, 18, 42},
  {FONT_KEY_LECO_42_NUMBERS, 13, 29},
#else
  {FONT_KEY_LECO_38_BOLD_NUMBERS, 11, 27},
  {FONT_KEY_LECO_32_BOLD_NUMBERS, 10, 22},
  {FONT_KEY_LECO_28_LIGHT_NUMBERS, 8, 20},
#endif
};

static Window *s_window;
static Layer *s_canvas;
static GDrawCommandImage *s_icons[6];
static const ScreenState *s_screen;
static GFont s_value_font;
static const CoordinateFont *s_value_metrics;
static bool s_font_precise;
static bool s_font_initialized;
static bool s_icons_inverted;
static uint8_t s_icon_errors;
static uint32_t s_capture;

#ifdef LARGE_COORDINATE_LAYOUT
static const uint32_t s_icon_resources[] = {
  0, RESOURCE_ID_ICON_LOCATION_25, RESOURCE_ID_ICON_LOCATION_PROBLEM_25,
  RESOURCE_ID_ICON_CLOCK_25, RESOURCE_ID_ICON_WARNING_25, RESOURCE_ID_ICON_FLASHLIGHT_25};
#else
static const uint32_t s_icon_resources[] = {
   0, RESOURCE_ID_ICON_LOCATION_18, RESOURCE_ID_ICON_LOCATION_PROBLEM_18,
  RESOURCE_ID_ICON_CLOCK_18, RESOURCE_ID_ICON_WARNING_18, RESOURCE_ID_ICON_FLASHLIGHT_18};
#endif

static Layout layout(void) {
#if defined(PBL_PLATFORM_EMERY)
  return (Layout){.status_h = 24, .label_y = 24, .label_h = 26, .label_ink_top = 10, .label_ink_bottom = 26, .footer_y = 200,
    .status_font = FONT_KEY_GOTHIC_18_BOLD, .label_font = FONT_KEY_GOTHIC_24_BOLD,
    .caption_font = FONT_KEY_GOTHIC_18_BOLD, .footer_font = FONT_KEY_GOTHIC_18};
#elif defined(PBL_PLATFORM_GABBRO)
  return (Layout){.label_y = 30, .label_h = 26, .label_ink_bottom = 28, .footer_y = 208, .round = true,
    .status_font = FONT_KEY_GOTHIC_18_BOLD, .label_font = FONT_KEY_GOTHIC_24_BOLD,
    .caption_font = FONT_KEY_GOTHIC_14_BOLD, .footer_font = FONT_KEY_GOTHIC_18};
#elif defined(PBL_ROUND)
  return (Layout){.label_y = 27, .label_h = 22, .label_ink_bottom = 21, .round = true,
    .status_font = FONT_KEY_GOTHIC_14_BOLD, .label_font = FONT_KEY_GOTHIC_18_BOLD,
    .caption_font = FONT_KEY_GOTHIC_14_BOLD};
#else
  return (Layout){.status_h = 20, .label_y = 18, .label_h = 22, .label_ink_top = 7, .label_ink_bottom = 19, .footer_y = 144,
    .status_font = FONT_KEY_GOTHIC_18_BOLD, .label_font = FONT_KEY_GOTHIC_18_BOLD,
    .caption_font = FONT_KEY_GOTHIC_14_BOLD, .footer_font = FONT_KEY_GOTHIC_18};
#endif
}

static void draw_text(GContext *context, const char *text, const char *font,
                      GRect rect, GTextAlignment alignment) {
  if (!text || !text[0] || !font) {
    return;
  }
  graphics_draw_text(context, text, fonts_get_system_font(font), rect,
                     GTextOverflowModeTrailingEllipsis, alignment, NULL);
}

static int text_width(const char *text, const char *font) {
  if (!text || !text[0]) {
    return 0;
  }
  return graphics_text_layout_get_content_size(text, fonts_get_system_font(font),
      GRect(0, 0, 500, 60), GTextOverflowModeWordWrap, GTextAlignmentLeft).w;
}

static const char *fit_font(const char *text, int width, const char *const *fonts,
                            unsigned count) {
  for (unsigned i = 0; i < count; ++i) {
    if (text_width(text, fonts[i]) <= width) return fonts[i];
  }
  return fonts[count - 1];
}

static void choose_font(const Layout *item, int16_t width) {
  int available = item->round ? width - 40 : width - 8;
  const char *widest = s_screen->precise ? "359.99" DEG : "359.9" DEG;
  unsigned i = 0;
#ifdef LARGE_COORDINATE_LAYOUT
  if (s_screen->precise) i = 1;
#endif
  while (i + 1 < ARRAY_LENGTH(s_coordinate_fonts) &&
         text_width(widest, s_coordinate_fonts[i].key) > available) ++i;
  s_value_metrics = &s_coordinate_fonts[i];
  s_value_font = fonts_get_system_font(s_value_metrics->key);
  s_font_precise = s_screen->precise;
  s_font_initialized = true;
}

static void draw_coordinates(GContext *context, GSize size, const Layout *item) {
  int width = size.w;
  int digit_top = s_value_metrics->ink_top;
  int digit_h = s_value_metrics->ink_h;
  int y = item->label_y - 2 + item->label_ink_bottom;
  // Chalk has no footer; reserve the narrow bottom of its circular display.
  int bottom = item->footer_font ? item->footer_y : size.h - 18;
  int spare = bottom - y - 2 * (CAPTION_INK_H + digit_h);
  GTextAlignment caption_align = item->round ? GTextAlignmentCenter : GTextAlignmentLeft;
  GTextAlignment value_align = item->round ? GTextAlignmentCenter : GTextAlignmentRight;
  const StringId captions[] = {STR_AZ, STR_EL};
  const char *values[] = {s_screen->azimuth, s_screen->elevation};

  // Distribute spare pixels across the five gaps, at most one pixel apart.
  for (int i = 0; i < 2; ++i) {
    int caption_y = y + ((2 * i + 1) * spare + 2) / 5;
    int value_y = y + CAPTION_INK_H + ((2 * i + 2) * spare + 2) / 5;
    draw_text(context, l10n(captions[i]), item->caption_font,
              GRect(item->round ? 0 : 4, caption_y - CAPTION_INK_TOP,
                    item->round ? width : 30, 20), caption_align);
    graphics_draw_text(context, values[i], s_value_font,
                       GRect(0, value_y - digit_top, width - (item->round ? 0 : 4), 60),
                       GTextOverflowModeFill, value_align, NULL);
    if (s_capture) {
      int ink_w = graphics_text_layout_get_content_size(values[i], s_value_font,
        GRect(0, 0, 500, 60), GTextOverflowModeFill, value_align).w;
      int x = item->round ? (width - ink_w) / 2 : width - 4 - ink_w;
      APP_LOG(APP_LOG_LEVEL_INFO, "TEST_ROW %lu %d %d %d %d %d",
              (unsigned long)s_capture, i, x, value_y, ink_w, digit_h);
    }
    y += CAPTION_INK_H + digit_h;
  }
}

static void draw_separator(GContext *context, int width, int y) {
  graphics_context_set_stroke_color(context, s_screen->inverted ? GColorWhite : GColorBlack);
  graphics_context_set_stroke_width(context, 1);
  graphics_draw_line(context, GPoint(0, y), GPoint(width, y));
}

static void draw_icon(GContext *context, unsigned icon, int x, int y, int size) {
  if (!icon || icon >= ARRAY_LENGTH(s_icons)) {
    return;
  }
  if (!s_icons[icon]) {
    int text_y = y + (size - STATUS_TEXT_INK_H) / 2 - GOTHIC_18_INK_TOP;
    draw_text(context, "!", FONT_KEY_GOTHIC_18_BOLD, GRect(x, text_y, size, size),
              GTextAlignmentCenter);
    return;
  }
  gdraw_command_image_draw(context, s_icons[icon], GPoint(x, y));
}

static GColor invert_icon_color(GColor color) {
  if (!gcolor_equal(color, GColorClear)) color.argb ^= 0x3f;
  return color;
}

static bool invert_icon_command(GDrawCommand *command, uint32_t index, void *context) {
  (void)index;
  (void)context;
  gdraw_command_set_fill_color(command, invert_icon_color(gdraw_command_get_fill_color(command)));
  gdraw_command_set_stroke_color(command, invert_icon_color(gdraw_command_get_stroke_color(command)));
  return true;
}

static void invert_icon(GDrawCommandImage *image) {
  gdraw_command_list_iterate(gdraw_command_image_get_command_list(image), invert_icon_command, NULL);
}

static void set_icon_theme(bool inverted) {
  if (s_icons_inverted == inverted) return;
  for (unsigned i = 1; i < ARRAY_LENGTH(s_icons); ++i) {
    if (s_icons[i]) invert_icon(s_icons[i]);
  }
  s_icons_inverted = inverted;
}

static void load_icon(unsigned icon) {
  if (!icon || icon >= ARRAY_LENGTH(s_icons) || s_icons[icon]) return;
  s_icons[icon] = gdraw_command_image_create_with_resource(s_icon_resources[icon]);
  if (!s_icons[icon]) {
    if (!(s_icon_errors & (1u << icon))) {
      ASTRO_LOG(APP_LOG_LEVEL_ERROR, "Could not load status icon %u", icon);
      s_icon_errors |= 1u << icon;
    }
  } else {
    if (s_icons_inverted) invert_icon(s_icons[icon]);
    s_icon_errors &= ~(1u << icon);
  }
}

static void unload_icon(unsigned icon) {
  if (!s_icons[icon]) return;
  gdraw_command_image_destroy(s_icons[icon]);
  s_icons[icon] = NULL;
}

static void update_icons(const ScreenState *state) {
  for (unsigned i = 1; i <= STATUS_ICON_WARNING; ++i) {
    if (i != state->icon) unload_icon(i);
  }
  load_icon(state->icon);
#if defined(PBL_ROUND)
  bool flashlight_visible = state->flashlight && state->icon == STATUS_ICON_NONE;
#else
  bool flashlight_visible = state->flashlight;
#endif
  if (flashlight_visible) {
    load_icon(5);
  } else {
    unload_icon(5);
  }
}

static void draw_footer(GContext *context, GRect bounds, const Layout *item) {
  if (!item->footer_font) {
    return;
  }
  int width = bounds.size.w;
  int footer_y = item->footer_y;
  int text_y = footer_y;
  if (!item->round) {
    draw_separator(context, width, footer_y);
    int top = footer_y + 1;
    text_y = top + (bounds.size.h - top - FOOTER_TEXT_INK_H) / 2 - GOTHIC_18_INK_TOP;
  }
  if (s_screen->no_coordinates) {
    if (!item->round) {
      draw_text(context, s_screen->reason, item->footer_font,
                GRect(4, text_y, width - 8, 24), GTextAlignmentCenter);
    }
    return;
  }
  char magnitude[32] = "";
  if (item->round) {
    // The shorter rise/set text goes on the lower line, where the circle is narrowest.
    int line_h = 18;
    int magnitude_w = width - 80;
    int event_w = width - 110;
    int block_h = FOOTER_TEXT_INK_H;
    if (s_screen->magnitude[0] && s_screen->event[0]) block_h += line_h;
    int y = footer_y + (bounds.size.h - footer_y - block_h) / 2 - GOTHIC_18_INK_TOP;
    if (s_screen->magnitude[0]) {
      snprintf(magnitude, sizeof(magnitude), "%s %s", l10n(STR_MAGNITUDE), s_screen->magnitude);
      if (text_width(magnitude, item->footer_font) > magnitude_w) {
        snprintf(magnitude, sizeof(magnitude), "%s %s", l10n(STR_MAG_SHORT), s_screen->magnitude);
      }
      draw_text(context, magnitude, item->footer_font,
                GRect((width - magnitude_w) / 2, y, magnitude_w, line_h + 4),
                GTextAlignmentCenter);
      y += line_h;
    }
    draw_text(context, s_screen->event, item->footer_font,
              GRect((width - event_w) / 2, y, event_w, line_h + 4), GTextAlignmentCenter);
    return;
  }
  if (s_screen->magnitude[0]) {
    snprintf(magnitude, sizeof(magnitude), "%s %s", l10n(STR_MAGNITUDE), s_screen->magnitude);
    int available = width - 8;
    if (text_width(s_screen->event, item->footer_font) + text_width(magnitude, item->footer_font) +
        (s_screen->event[0] ? 12 : 0) > available) {
      snprintf(magnitude, sizeof(magnitude), "%s %s", l10n(STR_MAG_SHORT), s_screen->magnitude);
    }
    if (text_width(s_screen->event, item->footer_font) + text_width(magnitude, item->footer_font) +
        (s_screen->event[0] ? 12 : 0) > available) {
      magnitude[0] = '\0';
    }
  }
  draw_text(context, s_screen->event, item->footer_font,
            GRect(4, text_y, width - 8, 24), GTextAlignmentLeft);
  draw_text(context, magnitude, item->footer_font,
            GRect(4, text_y, width - 8, 24), GTextAlignmentRight);
}

static void draw_main(GContext *context, GRect bounds, Layout *item) {
  int width = bounds.size.w;
  int icon_size = width >= 200 ? 25 : 18;
  if (!s_font_initialized || s_font_precise != s_screen->precise) {
    choose_font(item, width);
  }
  if (!item->round) {
    // The label's nominal bottom balances names with and without descenders.
    int label_h = item->label_ink_bottom - item->label_ink_top;
    int spare = item->footer_y - item->status_h - 1 - label_h -
                2 * (CAPTION_INK_H + s_value_metrics->ink_h);
    item->label_y = item->status_h + 1 + spare / 6 - item->label_ink_top + 2;
    int time_y = (item->status_h - STATUS_TEXT_INK_H + 1) / 2 - GOTHIC_18_INK_TOP;
    int icon_y = (item->status_h - icon_size) / 2;
    draw_text(context, s_screen->clock, item->status_font,
              GRect(4, time_y, 100, item->status_h), GTextAlignmentLeft);
    int icons = (s_screen->icon != STATUS_ICON_NONE) + s_screen->flashlight;
    draw_text(context, s_screen->temperature, item->status_font,
              GRect(0, time_y, width - 4 - icons * icon_size, item->status_h),
              GTextAlignmentRight);
    int x = width - icon_size;
    if (s_screen->icon != STATUS_ICON_NONE) {
      draw_icon(context, s_screen->icon, x, icon_y, icon_size);
      x -= icon_size;
    }
    if (s_screen->flashlight) draw_icon(context, 5, x, icon_y, icon_size);
    draw_separator(context, width, item->status_h);
    draw_text(context, s_screen->label, item->label_font,
              GRect(4, item->label_y - 2, width - 8, item->label_h), GTextAlignmentLeft);
  } else {
    char status[40];
    unsigned icon = s_screen->icon ? s_screen->icon : (s_screen->flashlight ? 5 : 0);
    bool show_temperature = width >= 200 && s_screen->temperature[0] && !icon;
    snprintf(status, sizeof(status), "%s%s%s", s_screen->clock,
             s_screen->clock[0] && show_temperature ? "  " : "",
             show_temperature ? s_screen->temperature : "");
    int time_y, icon_y;
#ifdef LARGE_COORDINATE_LAYOUT
    // Inset the row from the narrow top of the circle. Center both elements
    // on the same axis above the catalog's visible glyphs.
    int status_mid = item->label_y / 2 + 6;
    time_y = status_mid - STATUS_TEXT_INK_H / 2 - GOTHIC_18_INK_TOP;
    icon_y = status_mid - (icon_size - 1) / 2;
#else
    int status_mid = item->label_y - 10;
    time_y = status_mid - 4 - 5; // Gothic 14: 9 px of ink starting 5 px down.
    icon_y = status_mid - icon_size / 2;
#endif
    draw_text(context, status, item->status_font,
              GRect(0, time_y, width, 22), GTextAlignmentCenter);
    if (icon) {
      int x = width / 2 + text_width(status, item->status_font) / 2 + 3;
      if (x + icon_size > width - 35) x = width - 35 - icon_size;
      draw_icon(context, icon, x, icon_y, icon_size);
    }
    draw_text(context, s_screen->label, item->label_font,
              GRect(36, item->label_y - 2, width - 72, item->label_h), GTextAlignmentCenter);
  }
  draw_coordinates(context, bounds.size, item);
  draw_footer(context, bounds, item);
}

static void draw_banner(GContext *context, GRect bounds, const Layout *item) {
  int width = bounds.size.w, height = bounds.size.h;
  bool small = width < 200;
  int top = item->round ? (small ? 18 : 40) : 0;
  int side = item->round ? (small ? 24 : 36) : 4;
  if (s_screen->banner_message[0]) {
    GRect message_rect = GRect(side, height / 2 - 32, width - 2 * side, 80);
    if (s_screen->filter_notice) {
      int margin = item->round ? (small ? 30 : 40) : 8;
      message_rect = GRect(side, margin, width - 2 * side, height - 2 * margin);
    }
    GFont message_font = fonts_get_system_font(FONT_KEY_GOTHIC_24_BOLD);
    GSize message_size = graphics_text_layout_get_content_size(s_screen->banner_message,
      message_font, GRect(0, 0, message_rect.size.w, 1000),
      GTextOverflowModeWordWrap, GTextAlignmentCenter);
    if (message_size.h > message_rect.size.h) {
      message_font = fonts_get_system_font(FONT_KEY_GOTHIC_18_BOLD);
      message_size = graphics_text_layout_get_content_size(s_screen->banner_message,
        message_font, GRect(0, 0, message_rect.size.w, 1000),
        GTextOverflowModeWordWrap, GTextAlignmentCenter);
      if (message_size.h > message_rect.size.h) {
        message_font = fonts_get_system_font(FONT_KEY_GOTHIC_14_BOLD);
        message_size = graphics_text_layout_get_content_size(s_screen->banner_message,
          message_font, GRect(0, 0, message_rect.size.w, 1000),
          GTextOverflowModeWordWrap, GTextAlignmentCenter);
      }
    }
    if (s_screen->filter_notice && message_size.h < message_rect.size.h) {
      message_rect.origin.y += (message_rect.size.h - message_size.h) / 2;
      message_rect.size.h = message_size.h;
    }
    graphics_draw_text(context, s_screen->banner_message,
      message_font, message_rect,
      GTextOverflowModeWordWrap, GTextAlignmentCenter, NULL);
    return;
  }
  char index[24];
  snprintf(index, sizeof(index), "%u / %u", s_screen->banner_index, s_screen->banner_count);
  const char *title_fonts[] = {FONT_KEY_GOTHIC_18_BOLD, FONT_KEY_GOTHIC_14_BOLD};
  const char *title_font = fit_font(s_screen->banner_catalog, width - 2 * side,
                                    title_fonts, ARRAY_LENGTH(title_fonts));
  bool separate_index = item->round ||
    text_width(s_screen->banner_catalog, title_font) + text_width(index, FONT_KEY_GOTHIC_18_BOLD) >
    width - 2 * side - 8;
  draw_text(context, s_screen->banner_catalog, title_font,
            GRect(side, top, width - 2 * side, 22),
            separate_index ? GTextAlignmentCenter : GTextAlignmentLeft);
  draw_text(context, index, item->round ? FONT_KEY_GOTHIC_14_BOLD : FONT_KEY_GOTHIC_18_BOLD,
            separate_index ? GRect(0, top + 18, width, 22) : GRect(0, top, width - 4, 22),
            separate_index ? GTextAlignmentCenter : GTextAlignmentRight);
  int id_y = item->round && small ? 58 : height / 2 - (small ? 44 : 50);
  const char *primary = s_screen->banner_id[0] ? s_screen->banner_id : s_screen->banner_name;
  const char *secondary = s_screen->banner_id[0] ? s_screen->banner_name : "";
  const char *primary_fonts[] = {FONT_KEY_BITHAM_42_BOLD, FONT_KEY_BITHAM_30_BLACK,
                                 FONT_KEY_GOTHIC_28_BOLD, FONT_KEY_GOTHIC_24_BOLD,
                                 FONT_KEY_GOTHIC_18_BOLD};
  const char *primary_font = fit_font(primary, width - 2 * side,
                                     primary_fonts, ARRAY_LENGTH(primary_fonts));
  draw_text(context, primary, primary_font,
            GRect(side, id_y, width - 2 * side, 50), GTextAlignmentCenter);
  if (item->round && small) id_y -= 4;
  int secondary_y = id_y + 52;
  graphics_draw_text(context, secondary,
    fonts_get_system_font(width >= 200 ? FONT_KEY_GOTHIC_28_BOLD : FONT_KEY_GOTHIC_24_BOLD),
    GRect(side, secondary_y, width - 2 * side, 70),
    GTextOverflowModeWordWrap, GTextAlignmentCenter, NULL);
  if (s_screen->working) {
    int working_y = width >= 200 && height >= 220 ? secondary_y + 64 :
                    height - (item->round ? 48 : 34);
    draw_text(context, "...", FONT_KEY_GOTHIC_28_BOLD,
              GRect(side, working_y, width - 2 * side, 34), GTextAlignmentCenter);
  } else if (width >= 200 && height >= 220 && s_screen->banner_coordinates[0]) {
    const char *preview_fonts[] = {FONT_KEY_GOTHIC_18, FONT_KEY_GOTHIC_14};
    const char *preview_font = fit_font(s_screen->banner_coordinates, width - 2 * side,
                                        preview_fonts, ARRAY_LENGTH(preview_fonts));
    draw_text(context, s_screen->banner_coordinates, preview_font,
              GRect(side, secondary_y + 72, width - 2 * side, 30),
              GTextAlignmentCenter);
  }
}

static void canvas_update(Layer *layer, GContext *context) {
  if (!s_screen) return;
  GRect bounds = layer_get_bounds(layer);
  Layout item = layout();
  GColor background = s_screen->inverted ? GColorBlack : GColorWhite;
  GColor foreground = s_screen->inverted ? GColorWhite : GColorBlack;
  graphics_context_set_fill_color(context, background);
  graphics_fill_rect(context, bounds, 0, GCornerNone);
  graphics_context_set_text_color(context, foreground);
  graphics_context_set_stroke_color(context, foreground);
  if (s_screen->banner) draw_banner(context, bounds, &item);
  else draw_main(context, bounds, &item);
  if (s_capture) {
    APP_LOG(APP_LOG_LEVEL_INFO, "TEST_SCREEN %lu %d %d %d %s|%s|%s",
      (unsigned long)s_capture, s_screen->banner, s_screen->inverted,
      s_screen->precise, s_screen->label,
      s_screen->azimuth, s_screen->elevation);
    APP_LOG(APP_LOG_LEVEL_INFO, "TEST_DETAIL %lu %d %d %d %s|%s|%s|%s|%s",
      (unsigned long)s_capture, s_screen->flashlight, s_screen->filter_notice,
      s_screen->icon, s_screen->clock, s_screen->temperature,
      s_screen->event, s_screen->magnitude, s_screen->reason);
    if (s_screen->banner) {
      APP_LOG(APP_LOG_LEVEL_INFO, "TEST_BANNER %lu %s", (unsigned long)s_capture,
              s_screen->banner_message);
    }
    s_capture = 0;
  }
}

static void window_load(Window *window) {
  Layer *root = window_get_root_layer(window);
  s_canvas = layer_create(layer_get_bounds(root));
  if (!s_canvas) {
    ASTRO_LOG(APP_LOG_LEVEL_ERROR, "Could not create main display layer");
    return;
  }
  layer_set_update_proc(s_canvas, canvas_update);
  layer_add_child(root, s_canvas);
}

static void window_unload(Window *window) {
  (void)window;
  for (unsigned i = 1; i < ARRAY_LENGTH(s_icons); ++i) {
    unload_icon(i);
  }
  if (s_canvas) layer_destroy(s_canvas);
  s_canvas = NULL;
}

bool ui_init(ClickConfigProvider click_provider) {
  s_window = window_create();
  if (!s_window) {
    ASTRO_LOG(APP_LOG_LEVEL_ERROR, "Could not create application window");
    return false;
  }
  window_set_click_config_provider(s_window, click_provider);
  window_set_window_handlers(s_window, (WindowHandlers){.load = window_load, .unload = window_unload});
  window_stack_push(s_window, true);
  return s_canvas != NULL;
}

void ui_deinit(void) {
  if (s_window) window_destroy(s_window);
  s_window = NULL;
  s_screen = NULL;
}

void ui_set_click_config_provider(ClickConfigProvider click_provider) {
  if (s_window) window_set_click_config_provider(s_window, click_provider);
}

void ui_update(const ScreenState *state) {
  if (!state) return;
  set_icon_theme(state->inverted);
  update_icons(state);
  s_screen = state;
  if (s_canvas) layer_mark_dirty(s_canvas);
}

void ui_capture_next_frame(uint32_t request) {
  s_capture = request;
  if (s_canvas) layer_mark_dirty(s_canvas);
}

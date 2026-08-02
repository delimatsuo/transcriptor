/**
 * Visual system for T.A.R.S.
 *
 * Components keep inline styles but consume these roles instead of literals,
 * so spacing rhythm and type scale stay consistent across surfaces.
 */
export const tokens = {
  color: {
    text: {
      primary: "#1d1d1f",
      secondary: "#86868b",
      tertiary: "#aeaeb2",
      onAccent: "#ffffff",
    },
    surface: {
      base: "#ffffff",
      raised: "#fafafa",
      sunken: "#f5f5f7",
    },
    border: {
      subtle: "#f5f5f7",
      strong: "#d2d2d7",
    },
    accent: "#007aff",
    success: "#34c759",
    warn: "#ff9500",
    danger: "#ff3b30",
    dangerWash: "rgba(255, 59, 48, 0.06)",
    successWash: "rgba(52, 199, 89, 0.1)",
  },
  space: {
    xs: 4,
    sm: 8,
    md: 12,
    lg: 16,
    xl: 24,
    xxl: 32,
    xxxl: 40,
  },
  radius: {
    sm: 8,
    md: 12,
    lg: 16,
    pill: 100,
  },
  text: {
    micro: 10,
    caption: 11,
    small: 13,
    body: 15,
    title: 17,
    hero: 22,
    display: 28,
  },
} as const;

export type Tokens = typeof tokens;

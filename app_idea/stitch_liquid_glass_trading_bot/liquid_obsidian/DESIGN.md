---
name: Liquid Obsidian
colors:
  surface: '#111317'
  surface-dim: '#111317'
  surface-bright: '#37393e'
  surface-container-lowest: '#0c0e12'
  surface-container-low: '#1a1c20'
  surface-container: '#1e2024'
  surface-container-high: '#282a2e'
  surface-container-highest: '#333539'
  on-surface: '#e2e2e8'
  on-surface-variant: '#c1c6d7'
  inverse-surface: '#e2e2e8'
  inverse-on-surface: '#2f3035'
  outline: '#8b90a0'
  outline-variant: '#414755'
  surface-tint: '#adc6ff'
  primary: '#adc6ff'
  on-primary: '#002e69'
  primary-container: '#4b8eff'
  on-primary-container: '#00285c'
  inverse-primary: '#005bc1'
  secondary: '#f4fff5'
  on-secondary: '#003822'
  secondary-container: '#00ffab'
  on-secondary-container: '#007149'
  tertiary: '#ffb3ae'
  on-tertiary: '#68000b'
  tertiary-container: '#ff5352'
  on-tertiary-container: '#5c0008'
  error: '#ffb4ab'
  on-error: '#690005'
  error-container: '#93000a'
  on-error-container: '#ffdad6'
  primary-fixed: '#d8e2ff'
  primary-fixed-dim: '#adc6ff'
  on-primary-fixed: '#001a41'
  on-primary-fixed-variant: '#004493'
  secondary-fixed: '#4dffb2'
  secondary-fixed-dim: '#00e297'
  on-secondary-fixed: '#002112'
  on-secondary-fixed-variant: '#005234'
  tertiary-fixed: '#ffdad7'
  tertiary-fixed-dim: '#ffb3ae'
  on-tertiary-fixed: '#410004'
  on-tertiary-fixed-variant: '#930014'
  background: '#111317'
  on-background: '#e2e2e8'
  surface-variant: '#333539'
typography:
  display-lg:
    fontFamily: Inter
    fontSize: 48px
    fontWeight: '700'
    lineHeight: '1.1'
    letterSpacing: -0.02em
  headline-md:
    fontFamily: Inter
    fontSize: 24px
    fontWeight: '600'
    lineHeight: '1.3'
    letterSpacing: -0.01em
  body-lg:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '400'
    lineHeight: '1.5'
  data-table:
    fontFamily: JetBrains Mono
    fontSize: 14px
    fontWeight: '500'
    lineHeight: '1.2'
  label-sm:
    fontFamily: JetBrains Mono
    fontSize: 11px
    fontWeight: '700'
    lineHeight: '1'
    letterSpacing: 0.05em
rounded:
  sm: 0.25rem
  DEFAULT: 0.5rem
  md: 0.75rem
  lg: 1rem
  xl: 1.5rem
  full: 9999px
spacing:
  unit: 4px
  container-padding: 20px
  gutter: 16px
  panel-gap: 12px
---

## Brand & Style
The design system is centered on a "Liquid Glass" aesthetic, engineered specifically for high-frequency crypto trading environments where clarity and premium feel are paramount. The personality is high-tech and futuristic, utilizing deep obsidian depths to create a sense of infinite digital space.

The style leverages **Glassmorphism** as its core structural driver. Surfaces are treated as semi-transparent panels that "float" over a dark void, using background blurs to maintain legibility while preserving a sense of layered depth. Vibrant accent glows serve as functional light sources, guiding the user's eye to critical market movements and status updates. The emotional response is one of calm control within a complex, fast-moving financial ecosystem.

## Colors
The palette is optimized for OLED displays and low-light trading sessions. 

- **Backgrounds:** Use a base of `Neutral` (#0F1115). Surface containers utilize a slightly lighter obsidian tint with 60-80% opacity to allow background gradients to bleed through.
- **Electric Blue (Primary):** Used for primary actions, active states, and focus indicators.
- **Success Green:** Reserved strictly for "Buy" signals, profit percentages, and positive market trends.
- **Danger Red:** Reserved for "Sell" signals, loss indicators, and critical alerts.
- **Overlays:** Use a subtle "White Noise" or "Glass Grain" texture at 3% opacity on top of translucent panels to prevent color banding in the blurs.

## Typography
The typography system balances human-centric readability with technical precision. 

**Inter** is the workhorse for all UI labels, navigation, and headers, providing a clean and professional tone. **JetBrains Mono** is utilized for all numerical data, wallet addresses, and transaction hashes to ensure character distinction (e.g., distinguishing 0 from O) and to provide a "coded" feel that aligns with the bot-driven nature of the product. 

For mobile, `display-lg` should scale down to 32px to ensure pricing data remains within the viewport without horizontal scrolling.

## Layout & Spacing
This design system employs a **fluid grid** with strict safe-area margins. 

- **Grid:** A 12-column grid for desktop and a 4-column grid for mobile.
- **Rhythm:** All spacing must be multiples of 4px. Use 12px or 16px for internal card padding to maintain a compact, data-dense look.
- **Safe Areas:** On mobile, leave a 80px bottom margin to account for the floating frosted navigation bar. 
- **Density:** Elements should be tightly packed but separated by "light gaps"—thin areas of background dark space—to emphasize the floating nature of the panels.

## Elevation & Depth
Depth is not communicated via traditional shadows, but through **refractive layering**:

1.  **Level 0 (Floor):** Deep obsidian (#0F1115) with occasional vibrant mesh gradients (Blue/Green/Red) at 10% opacity in the corners.
2.  **Level 1 (Panels):** Background Blur (20px to 40px) with a 10% white tint. Outer border is a 1px solid stroke with 15% white opacity.
3.  **Level 2 (Active/Floating):** Background Blur (60px) with a 20% white tint. Outer border is a 1px solid stroke with 30% white opacity.
4.  **Accents:** "Glow" effects are achieved using drop shadows with 0 offset, high spread (20px+), and the accent color (Blue/Green/Red) at 40% opacity.

## Shapes
The shape language is "Soft-Tech." All containers and buttons use a **0.5rem (8px)** base radius. 

Larger containers (Cards) use `rounded-lg` (1rem/16px) to appear friendly despite the technical nature of the app. Interactive elements like input fields and toggles should never be sharp; the roundedness suggests a "liquid" or "molded glass" quality. Status indicators (dots) are perfectly circular to simulate LED hardware.

## Components
- **Translucent Cards:** Use Level 1 elevation. Include a subtle top-left light sweep (linear gradient) on the border to simulate a light source hitting the glass edge.
- **Frosted Navigation Bar:** Positioned at the bottom, detached from the screen edges by 16px. It should have a heavy background blur (50px) and a high-contrast white border.
- **Glowing Status Indicators:** Small circular badges. For "Bot Active," use Success Green with a 10px outer glow. For "Bot Paused," use a dim neutral gray.
- **Action Buttons:**
    - **Primary:** Solid Electric Blue with white text.
    - **Secondary/Glass:** Translucent background with a high-opacity white border.
- **Data Tables:** Use JetBrains Mono. Row separators should be 1px lines at 5% white opacity. Alternate row shading is not required; use hover-state glows instead.
- **Input Fields:** Darker than the card background (obsidian-inset) with a 1px border that glows Electric Blue when focused.
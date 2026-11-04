/** Keep custom accent colours readable in both workspace modes. */
export function applyTheme(accent: string, mode: "dark" | "light") {
  const channels = [1, 3, 5].map((i) => parseInt(accent.slice(i, i + 2), 16));
  const luminance = (rgb: number[]) =>
    rgb
      .map((c) => c / 255)
      .map((c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4))
      .reduce((v, c, i) => v + c * [0.2126, 0.7152, 0.0722][i], 0);
  const contrast = (a: number[], b: number[]) => {
    const x = luminance(a),
      y = luminance(b);
    return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
  };
  const background = mode === "light" ? [255, 253, 247] : [35, 39, 36];
  let ink = [...channels];
  for (let i = 0; i < 30 && contrast(ink, background) < 4.7; i++)
    ink = ink.map((c) => (mode === "light" ? c * 0.9 : c + (255 - c) * 0.1));
  const root = document.documentElement;
  root.style.setProperty("--accent", accent);
  root.style.setProperty("--accent-rgb", channels.join(","));
  root.style.setProperty("--accent-ink", `rgb(${ink.map(Math.round).join(",")})`);
  root.style.setProperty(
    "--on-accent",
    contrast(channels, [33, 29, 22]) >= contrast(channels, [255, 255, 255]) ? "#211d16" : "#ffffff",
  );
  root.dataset.theme = mode;
}

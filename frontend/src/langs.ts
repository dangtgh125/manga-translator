/** Target languages offered in the UI. Values are the codes the translation adapters understand
 * (see backend `translate/base.py` LANG_NAMES and `translate/google.py` _LANG); labels show the
 * short names users know (jp/cn). */
export const TARGET_LANGS: { code: string; label: string }[] = [
  { code: "vi", label: "Tiếng Việt (vi)" },
  { code: "en", label: "English (en)" },
  { code: "ja", label: "日本語 (jp)" },
  { code: "zh-Hans", label: "中文 (cn)" },
];

/** Options for a `<select>`: the known list, plus the current value when it is not in it so
 * a project created with another code still shows what it has. */
export function targetLangOptions(current?: string): { code: string; label: string }[] {
  if (!current || TARGET_LANGS.some((l) => l.code === current)) return TARGET_LANGS;
  return [...TARGET_LANGS, { code: current, label: current }];
}

/** Items of `items` shown on 1-based page `page` of size `size`; `pages` is the page count (≥ 1). */
export function paginate<T>(items: T[], page: number, size: number): { items: T[]; page: number; pages: number } {
  const pages = Math.max(1, Math.ceil(items.length / size));
  const p = Math.min(Math.max(1, page), pages);
  return { items: items.slice((p - 1) * size, p * size), page: p, pages };
}

/** What the renderer does with lettering drawn straight over art that it cannot separate. */
export const SFX_POLICIES = [
  { code: "keep", label: "Giữ nguyên (bỏ qua vùng)" },
  { code: "white_box", label: "Hộp trắng + chữ dịch" },
] as const;
export type SfxPolicy = (typeof SFX_POLICIES)[number]["code"];

export function sfxPolicy(settings: Record<string, unknown> | undefined): SfxPolicy {
  const v = settings?.sfx_policy;
  return SFX_POLICIES.some((p) => p.code === v) ? (v as SfxPolicy) : "keep";
}

import { LANGS, useLang } from "../lib/i18n";

// Compact en · ru · it toggle, shown in the persistent meta bar. Two-letter codes
// keep it language-neutral. The choice persists (localStorage) and also sets the
// language the read comes back in — captured at upload time.
export default function LangSwitcher() {
  const [lang, setLang] = useLang();
  return (
    <span className="langsw">
      {LANGS.map((l) => (
        <button
          key={l}
          type="button"
          className={l === lang ? "on" : ""}
          onClick={() => setLang(l)}
          aria-label={`language: ${l}`}
          aria-pressed={l === lang}
        >
          {l}
        </button>
      ))}
    </span>
  );
}

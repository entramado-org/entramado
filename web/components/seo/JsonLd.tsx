/* Server component: emits a schema.org JSON-LD block (spec 035).
 *
 * Editorial rule (presunción de inocencia): structured data is a
 * machine-readable ASSERTION. Only neutral identity/navigation facts belong
 * here (names, jobTitle, breadcrumbs, dates) — NEVER accusations, charges or
 * case allegations. The prose carries the nuance with its citations. */
export default function JsonLd({ data }: { data: Record<string, unknown> }) {
  return (
    <script
      type="application/ld+json"
      // JSON.stringify output is escaped to keep any `</script>` in content
      // from breaking out of the tag (defense in depth; our data is curated).
      dangerouslySetInnerHTML={{
        __html: JSON.stringify(data).replace(/</g, "\\u003c"),
      }}
    />
  );
}

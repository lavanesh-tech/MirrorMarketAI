/**
 * What the product does, shown rather than described: a verdict in which every claim
 * is marked and traceable to a numbered source. Static sample data, clearly labelled.
 */
const SOURCES = [
  { id: "E1", from: "Manufacturer spec sheet", quote: "Memory: 16 GB LPDDR5" },
  { id: "E2", from: "Manufacturer spec sheet", quote: "Display: 14.0-inch, 2880 × 1800" },
  { id: "E3", from: "Retailer listing", quote: "Now $1,299, was $1,499" },
  { id: "E4", from: "Owner review", quote: "The keyboard feels mushy after a month" },
];

function Cite({ id }: { id: string }) {
  return <sup className="ml-0.5 font-semibold text-action">{id}</sup>;
}

export function EvidenceSpecimen() {
  return (
    <figure className="max-w-xl">
      <figcaption className="mb-4 text-sm text-muted">Example verdict (sample data)</figcaption>
      <blockquote className="font-[family-name:var(--font-display)] text-[1.6rem] leading-[1.35] font-medium">
        The Acme L14 fits your brief: <span className="mark">16 GB of memory</span>
        <Cite id="E1" />, <span className="mark">a 14-inch screen</span>
        <Cite id="E2" /> and <span className="mark">$1,299 against a $1,500 budget</span>
        <Cite id="E3" />. One owner reports <span className="mark">a mushy keyboard</span>
        <Cite id="E4" />.
      </blockquote>
      <ol className="mt-8 space-y-2 border-t border-line pt-5">
        {SOURCES.map((source) => (
          <li key={source.id} className="grid grid-cols-[2.5rem_1fr] gap-x-2">
            <span className="font-semibold text-action">{source.id}</span>
            <span>
              <span className="text-muted">{source.from}:</span> “{source.quote}”
            </span>
          </li>
        ))}
      </ol>
    </figure>
  );
}

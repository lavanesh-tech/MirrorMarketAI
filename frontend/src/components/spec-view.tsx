import {
  budgetText,
  criterionLabel,
  criterionTarget,
  type Criterion,
  type RequirementSpec,
} from "@/lib/requirements";

type Props = {
  spec: RequirementSpec;
  /** When given, priorities and importance can be changed and criteria removed. */
  onChange?: (spec: RequirementSpec) => void;
};

/** A requirement spec as a buyer would read it. Editable when `onChange` is passed. */
export function SpecView({ spec, onChange }: Props) {
  const criteria = spec.criteria ?? [];
  const facts: Array<[string, string]> = [];
  if (spec.category) facts.push(["Looking for", spec.category]);
  if (spec.budget) facts.push(["Budget", budgetText(spec.budget)]);
  if (spec.use_cases?.length) facts.push(["Used for", spec.use_cases.join(", ")]);
  if (spec.owned_devices?.length) facts.push(["Must work with", spec.owned_devices.join(", ")]);
  if (spec.excluded_brands?.length)
    facts.push(["Brands to avoid", spec.excluded_brands.join(", ")]);

  function update(index: number, change: Partial<Criterion>) {
    onChange?.({
      ...spec,
      criteria: criteria.map((c, i) => (i === index ? { ...c, ...change } : c)),
    });
  }

  if (facts.length === 0 && criteria.length === 0) {
    return <p className="text-muted">Nothing specific yet.</p>;
  }

  return (
    <div className="space-y-5">
      {facts.length > 0 ? (
        <dl className="grid max-w-xl grid-cols-[auto_1fr] gap-x-6 gap-y-1.5">
          {facts.map(([term, detail]) => (
            <div key={term} className="contents">
              <dt className="text-muted">{term}</dt>
              <dd className="font-semibold">{detail}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      {criteria.length > 0 ? (
        <table className="w-full max-w-3xl border-collapse text-left">
          <caption className="sr-only">Criteria</caption>
          <thead>
            <tr className="border-b-2 border-ink text-sm">
              <th scope="col" className="py-2 pr-4 font-semibold">
                Criterion
              </th>
              <th scope="col" className="py-2 pr-4 font-semibold">
                Target
              </th>
              <th scope="col" className="py-2 pr-4 font-semibold">
                Priority
              </th>
              {onChange ? (
                <>
                  <th scope="col" className="py-2 pr-4 font-semibold">
                    Importance
                  </th>
                  <th scope="col" className="py-2 font-semibold">
                    <span className="sr-only">Remove</span>
                  </th>
                </>
              ) : null}
            </tr>
          </thead>
          <tbody>
            {criteria.map((criterion, index) => {
              const label = criterionLabel(criterion.key);
              const must = criterion.priority === "MUST";
              return (
                <tr key={`${criterion.key}${criterion.operator}`} className="border-b border-line">
                  <th scope="row" className="py-2.5 pr-4 font-semibold">
                    {label}
                  </th>
                  <td className="py-2.5 pr-4">{criterionTarget(criterion)}</td>
                  <td className="py-2.5 pr-4">
                    {onChange ? (
                      <select
                        aria-label={`Priority of ${label}`}
                        className="field py-1.5"
                        value={criterion.priority}
                        onChange={(event) =>
                          update(index, { priority: event.target.value as Criterion["priority"] })
                        }
                      >
                        <option value="MUST">Must have</option>
                        <option value="SHOULD">Nice to have</option>
                      </select>
                    ) : must ? (
                      <span className="tag">Must have</span>
                    ) : (
                      `Nice to have, importance ${criterion.weight} of 5`
                    )}
                  </td>
                  {onChange ? (
                    <>
                      <td className="py-2.5 pr-4">
                        {must ? (
                          <span className="text-muted">Rules products out</span>
                        ) : (
                          <select
                            aria-label={`Importance of ${label}`}
                            className="field py-1.5"
                            value={criterion.weight}
                            onChange={(event) =>
                              update(index, { weight: Number(event.target.value) })
                            }
                          >
                            {[1, 2, 3, 4, 5].map((weight) => (
                              <option key={weight} value={weight}>
                                {weight} of 5
                              </option>
                            ))}
                          </select>
                        )}
                      </td>
                      <td className="py-2.5">
                        <button
                          type="button"
                          className="link"
                          aria-label={`Remove ${label}`}
                          onClick={() =>
                            onChange({ ...spec, criteria: criteria.filter((_, i) => i !== index) })
                          }
                        >
                          Remove
                        </button>
                      </td>
                    </>
                  ) : null}
                </tr>
              );
            })}
          </tbody>
        </table>
      ) : null}
      {onChange && criteria.some((c) => c.priority === "MUST") ? (
        <p className="max-w-xl text-sm text-muted">
          A product that misses a must-have is ruled out, whatever its score.
        </p>
      ) : null}
    </div>
  );
}

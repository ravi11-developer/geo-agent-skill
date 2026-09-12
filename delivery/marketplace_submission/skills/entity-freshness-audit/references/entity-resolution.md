# Entity resolution: why a canonical anchor

## The failure it fixes

Counting distinct name strings looks reasonable and breaks immediately on real sites.
Crawling four pages of nobroker.in yields: `NoBroker`, `Online Rent Agreement`,
`Online Rental Agreement`, `NoBroker Technologies Solutions`. Three of those are page
titles, not company names. A naive "3+ distinct names" rule reports entity ambiguity on
a site that is perfectly consistent.

## The rule

Anchor first, then count only variants *of the anchor*:

```
canonical = medoid(names that sit in a strong slot or recur)
variants  = names within 2.2x length AND (similarity >= 0.5 OR shared 4-char stem)
fire when 1 + len(variants) >= 3
```

## Why the medoid, not the most frequent

On site-008 of the synthetic suite the footer reads "© 2020 PeakCloud Inc." while the
title and H1 read "CloudPeak Technologies". Both occupy two slots, so frequency ties;
tie-breaking on strong slots picks `peakcloud`, and anchoring there hides the conflict
because the other spellings are not similar enough to a *variant* spelling. The medoid -
the name closest to all the others - picks `cloudpeak technologies`, and the family
(`Cloud Peak Tech`, `CP Technologies`, `PeakCloud`) resolves correctly.

## Worked examples

| site | canonical | variants found | verdict |
|---|---|---|---|
| NovaTech (synthetic 005) | novatech industries | nova technical, novartech, novatech holdings group, nova manufacturing | fires - 5 spellings |
| CloudPeak (synthetic 008) | cloudpeak technologies | cloud peak tech, cp technologies, peakcloud | fires - 4 spellings |
| Helios (synthetic 014) | helios robotics | helios robotic systems, helios systems group, heliobotics | fires - drift on sub-pages only |
| Harborline (synthetic 011) | harborline logistics | - (`Harborline Logistics B.V.` normalises to the same key) | clean |
| nobroker.in | nobroker | - (page titles excluded by the anchor) | clean |
| render.com | render | - (`Scaling Render Services` exceeds the length bound) | clean |
| Acme Cloud (synthetic 007) | acme cloud solutions | acme cloud | 2 spellings -> recommendation, not a finding |

# Fact families

An AI assistant answering "what does X cost", "who are they", "how do I reach them"
needs specific classes of fact. These four cover the overwhelming majority of
commercial questions and are cheap to detect language-agnostically.

## pricing
Currency symbols followed by digits (`$`, `€`, `£`, `₹`), ISO codes (`1299 USD`),
or period pricing (`per month`, `per user`, `/mo`, `/yr`). Presence means a crawler
can quote a number; absence on a page headed "Pricing" is the classic image-or-JS trap.

## contact
An email address or a phone number of >= 9 digits. This is the fact most often locked
into a contact-card image, and the one an assistant most often needs to complete a task.

## company_facts
Founding, headquarters, employee or customer counts, revenue, certifications
(`ISO 27001`, `SOC 2`), uptime/SLA figures, thousands-separated numbers, percentages.
These are what an entity summary is built from.

## product_detail
Structural rather than lexical: at least two headed sections each followed by >= 15
words of prose, or >= 120 words overall. It distinguishes a page that *describes*
products from a page that merely lists names.

## Why coverage, not keywords

Counting fact families makes the check domain-agnostic: it works for a hospital, a
pump manufacturer and a SaaS product without per-vertical rules, and it degrades
gracefully on a language the auditor does not model, because currency, digits and
contact patterns survive translation better than vocabulary does.

# Five-minute architecture talk

Speaking notes for the judging conversation. Roughly 750 words, which is five
minutes at a comfortable pace. Have `presentation.html` open on the architecture
diagram while you talk.

---

## 0:00 — 0:40 · The one idea

> We built three forecasting systems, independently, and the numbers we submitted
> are what those three agree on.
>
> But before the three systems, there's one idea underneath all of it: **a date
> cutoff.**
>
> Every document in the corpus has a publication date. So we built the whole
> system around a single question — *what was knowable on a given day?* One
> global guard answers that. Every piece of retrieval has to ask it for
> permission, and if no cutoff is configured, it raises an error instead of
> returning data.
>
> That means you can't accidentally look at the future. Not "we were careful" —
> you literally can't.

---

## 0:40 — 1:40 · Walk the diagram

*(Point at the diagram, top to bottom.)*

> Across the top, that red band is the cutoff. Everything below it is filtered.
>
> Underneath, four evidence channels. The frozen corpus — 1,139 filings and
> transcripts. Sell-side consensus — analyst estimates, 30 for Home Depot, 25 for
> ADI, 17 for Deere. Reported history, which we read out of earnings releases
> rather than 10-Qs, because releases state figures in prose with their units and
> 10-Qs bury them in tables. And public research, cached with its retrieval date.
>
> Those feed three systems, in the three coloured boxes. Same brief, same corpus,
> same twelve targets. **No shared code.**
>
> Their outputs meet in the black box: the consensus. Then validation. Then the
> four workbooks.

---

## 1:40 — 2:30 · Why three, and how the vote works

> Three people building three systems sounds like waste. It isn't, because the
> errors are independent. When three systems built from different code make
> different mistakes, the mistakes partly cancel. That only works if nobody
> copied anybody, which is why we kept them separate.
>
> The vote is deliberately blunt. Sort the three numbers. If one is more than
> twice as far from the middle as the other, it's an outlier — drop it, average
> the two that agree. Otherwise take the median. With three data points, anything
> cleverer is fitting noise.
>
> Here's it working. *(Point at the worked example.)* ADI's adjusted gross
> margin. My system said 69.2. Adrian's said 73.0. Dimitris's said 74.1. The gap
> on the low side is 3.8, on the high side 1.1. So mine got voted out, and the
> answer is 73.55.
>
> **The filing says 73.0.** My system was wrong, and the vote removed it without
> anyone knowing in advance which one was wrong.

---

## 2:30 — 3:20 · What's good about this

> Three things.
>
> **First, it's measured.** One of the three systems can replay the past
> honestly. We take a quarter whose result we already know, set the cutoff to the
> day before it was announced, and forecast it. 32 replays. The guard checked
> clean every time. So we can tell you how well the method actually works, not
> just what it predicted.
>
> And the backtest wasn't a report card at the end — it drove the design. It
> found three bugs we'd never have caught by reading code. We were reading
> Deere's quarterly *dividend* as its earnings per share, at every single event.
>
> **Second, everything traces.** Every number goes back to a document you can
> open, with the date and the sentence.
>
> **Third, it degrades instead of failing.** A missing forecast scores the
> maximum penalty. So if a model call fails or we run out of budget, the metric
> falls back to a simpler path. There's no path through this system that produces
> an empty cell.

---

## 3:20 — 4:20 · What's weak about it

> I'd rather tell you than have you find it.
>
> **The backtest has a blind spot, and we found it late.** A low error proves the
> method is *self-consistent*, not that it read the right line item. The forecast
> and the "actual" are extracted by the same code — so if it consistently grabs
> the GAAP number instead of the adjusted one, both agree and the error looks
> small while the submitted number is the wrong quantity. That's exactly what
> happened with ADI's gross margin. The three-system vote caught it precisely
> because the other two don't share that bias.
>
> **Three points is a thin ensemble.** It rejects an obvious outlier. It won't
> save us if two systems are wrong the same way.
>
> **And the consensus channel is a rail, not a target.** The accuracy prize
> divides our error by Wall Street's. If we just matched consensus we'd score
> exactly 1.0 — a guaranteed tie, and a guaranteed non-win. So we stay near it
> and deviate where we have evidence. On the six metrics where we have consensus,
> we're within 1.7% of it.

---

## 4:20 — 5:00 · Close

> So: one cutoff that makes the whole thing honest. Four channels. Three
> independent systems. A blunt vote that removed a real error today. And a
> backtest that tells you how much to trust it.
>
> The thing I'd want you to take away is the cutoff. It's one mechanism doing
> three jobs — it makes the backtest honest, it makes this run reproduce exactly
> next week, and if you drop the flag the whole thing becomes a live forecaster
> for the next earnings season.

---

## Likely questions

**"Why not just merge the three systems?"**
> An hour before the deadline, merging three codebases risks all three and buys
> nothing the numbers don't already give. The ensemble works on outputs, not
> modules. We could always merge afterwards.

**"How do you know the agent isn't hallucinating?"**
> It only gets asked about metrics the backtest says the deterministic path
> handles badly — six of twelve. Its answer has to come with a derivation and
> cited passages, and it faces the same unit and range checks as any other
> number. When it fails those, we keep the deterministic figure.

**"What did the model actually contribute?"**
> Hays never states net fees as a number — its trading statements give regional
> growth rates. Reconstructing a level means taking a base and applying growth
> weighted by region. That's arithmetic over read evidence. Regular expressions
> can't do it; the model did, and showed its working.

**"What would you do next?"**
> Fix the extractor's GAAP-versus-adjusted confusion properly. We tried it today,
> measured it, and it made three other metrics worse, so we reverted it rather
> than ship a change we'd measured to be harmful.

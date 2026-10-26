# Sponsoring Airflow Migration Verifier

I maintain this action alone, alongside college and a part-time job. Sponsorship pays for the time it takes to keep the rules current as each Airflow 3.x release ships: new removals, new defaults, new ruff `AIR3` rules, and the render check re-run against each new version.

[Sponsor on GitHub](https://github.com/sponsors/patrickballard). There are four tiers. Each one lists exactly what you get and what's out of scope.

## $5/month: Sponsor badge

**You get:** the sponsor badge GitHub shows on your profile, and my thanks.

**Out of scope:** everything else.

## $25/month: your logo or name in the README

**You get:**
- Your logo, or your name if you prefer, in the Sponsors section of the [README](README.md#sponsors), linked to a URL you choose.
- It's added **within 7 days** of the sponsorship starting, and **stays while the sponsorship is active**. It's removed when the sponsorship ends.
- To send a logo, open an issue with it attached: an SVG or PNG on a transparent background, plus the link. Without a logo, your name is shown, linked to your GitHub profile.

**Out of scope:**
- A placement anywhere other than the README's Sponsors section: not the top of the README, not the docs, not the Marketplace listing.
- Any statement that you use or endorse the project. I won't add one unless you write it.

I may decline a logo or link I can't show in a public README. If I do, I'll show your name instead.

## $100/month: bug reports prioritised

**You get:**
- Everything in the $25 tier.
- File bugs with the [Sponsor bug report](https://github.com/patrickballard/airflow-migration-verifier/issues/new?template=sponsor-bug-report.yml) template. It adds the `sponsor-priority` label.
- Issues with that label are **triaged before non-sponsor issues**.
- A **best-effort first response within 5 working days** (Monday to Friday).

**This is not an SLA, and it isn't a fix guarantee.** "Triaged" means I read the issue, try to reproduce it, and reply with what I found and what happens next. Some bugs will be fixed quickly. Some will take longer. Some won't be fixed at all, for example a request that's really a new feature, or a problem outside this action, in Airflow, a provider or your environment. In those cases I'll say so.

**Out of scope:**
- Response or resolution times beyond the best-effort first response.
- Calls, video meetings, private support channels (Slack, Discord, email threads), and help with your own DAGs or deployment beyond the bug you reported.
- Feature requests that jump the queue. Open them as normal issues.

The label is for active $100/month sponsors. I check the sponsoring account named in the issue, and remove the label if the sponsorship isn't active.

## $1,000 one-time: one custom migration rule

**You get:** one rule built for your codebase. It **detects one deprecated pattern or internal convention, and comes with the tests that verify it**. It's delivered as **one rule file plus its tests**, which you add to `.airflow-verifier/rules/` in your repo. From then on, the action runs it in both modes, next to the bundled rules:
- **verify mode** lists every site it finds in your pull requests;
- **migrate mode** lists them too, or rewrites them, and the rewrite goes through the same DAG import check and render check as the bundled rules.

[docs/writing-rules.md](docs/writing-rules.md) shows the format. It's the same format the example rules there use.

**How it works:**
1. Open a [Request a custom rule](https://github.com/patrickballard/airflow-migration-verifier/issues/new?template=request-custom-rule.yml) issue with the pattern, a sanitized example DAG snippet, and the fix you expect.
2. We agree the scope in that issue: what the rule matches, what it deliberately doesn't match, whether it rewrites, and a delivery date. **Nothing is paid before the scope is agreed.**
3. You sponsor the one-time $1,000 tier.
4. I deliver the rule file and its tests on the issue, by the agreed date.

**Complexity limits**, stated up front:
- **One pattern.** One deprecated API, one internal helper, or one convention. Variations of the same pattern count as one: different import styles, aliases, keyword or positional use. Unrelated patterns are separate rules.
- **Source only.** The rule must recognise the pattern from the text of one file at a time: a Python DAG file, or a template file such as `.sql` or `.sh`. It can't run your code, import your packages, or reach connections, Variables, XCom, a database or any other service.
- **Rewrites only when they're mechanical.** An automatic rewrite is included when it's a fixed transformation of the matched code that we've agreed is equivalent. Anything that needs judgment is reported for a human instead.
- **Tested against your examples.** The tests cover the snippets you provide in the issue (up to 5 that should match and 5 that shouldn't), plus the edge cases I find.
- **Public scoping.** Scoping happens in a public issue, so examples must be sanitized. If the pattern can't be described publicly, it's out of scope.

**Out of scope:**
- A second pattern. That's a second rule.
- Access to your repository, running the rule on your codebase for you, or changes to your CI, deployment or Airflow configuration.
- Calls or workshops.
- Maintaining the rule after delivery. The files are yours to keep, change and run. Bugs in it can be reported like any other issue.

## What no tier includes

No tier includes an SLA, calls, Slack or other private access, workshops, consulting, or a guaranteed fix. If it's not listed in the tier above, it isn't part of the sponsorship.

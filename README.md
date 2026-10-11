# Dihadi

*Dihadi* is the Hindi word for a day's wage, which is what this protects.

**Problem.** Heatwaves stop outdoor work and monsoon downpours shut shops and lanes. For India's daily-wage workers, either one means a day with no earnings, and these days are becoming more common as the climate warms. Weather-triggered cover can pay them quickly with no claim forms, but only if it is priced for the climate that is coming, not the one that has passed.

**Solution.** Dihadi lets a workers' group price that cover from 45 years of its own city's weather and the IPCC's warming projections. It then runs the cover as a fund on AWS: it checks the weather every morning, records each payout owed, and warns members a day ahead, by voice in Hindi or English for members who can't read.

Track: Heat and Water (heatwaves, floods, monsoon waterlogging).

## The model is copied from one that already works

SEWA (the Self Employed Women's Association) runs parametric heat cover for 50,000 women workers with Climate Resilience for All, ICICI Lombard and Swiss Re. In Ahmedabad it pays a fixed amount when the temperature stays above 43.6 °C for two days in a row, and more above 44.1 °C. Members pay part of the premium and the partner pays the rest. Dihadi's defaults use the same structure:

| Term | Heat | Heavy rain |
|---|---|---|
| Event | 2+ days in a row at or above 43.6 °C | a day with 64.5 mm or more (IMD "heavy") |
| Severe | 2+ days in a row at or above 44.1 °C | 115.6 mm or more (IMD "very heavy") |
| Payout per member | ₹750, or ₹1,250 if severe | ₹500, or ₹1,000 if severe |
| Limit | 3 payouts a year | 3 payouts a year |
| Who pays | members 40%, sponsor 60% | members 40%, sponsor 60% |

A sponsor can be an NGO, a company's CSR budget, the contractor, or the state Building and Other Construction Workers Welfare Board.

## Who uses it

- **Group leader** (`index.html`): types the city, sees what the cover would have paid every year since 1980, how warming changes that, and the fair premium split between members and the sponsor. Creates the fund in one click.
- **Fund operator** (`fund.html`): money in from members and the sponsor, payouts by event, the balance against a 1-in-200 year, and a three-day forecast.
- **Member** (`member.html`): no reading needed. Today and tomorrow as big pictures and colours (tick: work; cross: stop), what to do as pictures (no work from 12 to 4, drink water, rest in shade), money received as rupee notes, and one button that reads it all aloud in Hindi or English through Amazon Polly.

## The actuarial method

1. **Event history.** For the chosen city, every day from 1980 to 2024 (ERA5 reanalysis) is scanned for events and severe events under the cover's terms. This gives a "burn" history: what the cover would have paid each year.
2. **Linking events to warming.** Events per year are regressed on that year's global temperature with a Poisson GLM, log(events) = a + b × warming, fitted by iteratively reweighted least squares. Standard errors are inflated for over-dispersion (quasi-Poisson). exp(b) is how much each extra 1 °C multiplies events in this city. Global temperature is HadCRUT5, re-based to 1850–1900 to sit on the IPCC's scale; 2015–2024 averaged 1.26 °C.
3. **Credibility.** Forty-five years is a short record, so the city's slope is blended with a prior: Z = τ² / (τ² + se²), Bühlmann style. The prior is pooled across 12 Indian cities with a random-effects model (DerSimonian–Laird) by `backend/scripts/build_prior.py`. Until that has been run, it comes from IPCC AR6 (Figure SPM.6: 1-in-10-year hot extremes are 2.8× as frequent at 1 °C of warming and 5.6× at 2 °C).
4. **Future rate.** Events at a future warming level (IPCC AR6 Table SPM.1: SSP1-2.6, SSP2-4.5, SSP5-8.5 for 2021–40, 2041–60, 2081–2100) are today's fitted rate × exp(b × extra warming). The severe share is fitted the same way.
5. **Exact loss distribution.** Payouts in a year form a compound distribution: a Poisson number of events, each severe or not (binomial), capped. It is computed exactly on a lattice, with no simulation noise.
6. **Premium.** Expected payout + 6% cost of capital on (1-in-200 payout − expected payout) (the Solvency II risk-margin rate), grossed up for running costs, then split between members and the sponsor. A range is shown by repricing at the slope's 95% interval.
7. **Group risk.** Everyone in a city has the same bad year, so the group's 1-in-200 payout is members × the individual one. The fund page compares the balance with it and shows the gap to reinsure or sponsor.

The browser and Lambda run identical pricing code (they agree to 1e-12).

## Data

| Data | Source |
|---|---|
| Daily max temperature and rainfall, 1980–2024 | ERA5 reanalysis (ECMWF) via [Open-Meteo](https://open-meteo.com/en/docs/historical-weather-api), about 25 km grid |
| Forecast and recent days | Open-Meteo forecast API |
| Global mean temperature | [HadCRUT5](https://www.metoffice.gov.uk/hadobs/hadcrut5/), Met Office Hadley Centre and UEA |
| Warming by scenario and period; prior | [IPCC AR6 WG1 SPM](https://www.ipcc.ch/report/ar6/wg1/chapter/summary-for-policymakers/), Table SPM.1 and Figure SPM.6 |
| Product terms | SEWA heat cover, [NPR, July 2025](https://www.kpbs.org/news/health/2025/07/16/on-super-hot-days-this-insurance-plan-pays-out-cash-for-lost-wages) |
| Rain categories | India Meteorological Department |
| Cost of capital | [Solvency II, Delegated Regulation (EU) 2015/35](https://eur-lex.europa.eu/eli/reg_del/2015/35/oj), Article 39 |

## AWS

| Piece | AWS service | Job |
|---|---|---|
| Web pages | AWS Amplify Hosting | Leader, fund and member pages; redeploys on every push |
| API | Amazon API Gateway (HTTP API) | Seven routes in front of Lambda |
| Model and funds | AWS Lambda (Python 3.12, arm64) | Event history, GLM and credibility, exact pricing, fund records |
| Daily check | Amazon EventBridge Scheduler + Lambda | 09:00 Asia/Kolkata, every fund |
| Data | Amazon DynamoDB | Funds, payout events, and a 30-day cache of each city's 45-year series |
| Alerts | Amazon SNS | Payout notices and next-day warnings by email |
| Voice | Amazon Polly | Reads the member page aloud in Hindi or Indian English |
| Infrastructure | AWS SAM / CloudFormation | The whole backend from one template |

```
 index.html (leader)    fund.html (operator)    member.html (worker, voice)
        └──────────────────────┼──────────────────────┘
                     API Gateway HTTP API
                               │
                    Lambda: app.handler ── Open-Meteo (ERA5 history, forecast)
                     │          │
                DynamoDB      Polly
                     ▲
     Lambda: monitor.handler ◄── EventBridge Scheduler, 09:00 IST ──► SNS ──► email
```

| Route | Does |
|---|---|
| `POST /calibrate` | City, terms → 45-year event history, GLM fit, credibility, today's rate, trigger table |
| `POST /simulate` | Exact pricing today and in a warmer climate |
| `POST /groups` | Creates a fund |
| `GET /groups/{id}` | Fund, payouts, balance, 3-day forecast |
| `POST /groups/{id}/check` | Records events from the last 14 days now |
| `GET /groups/{id}/member?lang=hi` | The member's screen in Hindi or English |
| `GET /groups/{id}/speech?lang=hi` | The same, spoken by Polly as MP3 |

## Run it on your laptop

Needs Python 3.10 or newer and internet (for the weather). Nothing on AWS is touched; the database lives in memory.

```
python -m pip install -r backend/requirements-dev.txt
python backend/scripts/build_prior.py      # once: pools 12 Indian cities into the prior (~2 min)
python backend/local_server.py
# open http://localhost:8000
```

Add `--fake-weather` to run offline on a fixed sample series (the numbers are then not real). Run the 9 am check on demand with `curl -X POST http://localhost:8000/api/_monitor` (PowerShell: `Invoke-RestMethod -Method Post http://localhost:8000/api/_monitor`).

## Deploy

Needs the AWS CLI (configured) and the SAM CLI. Run `build_prior.py` first so `backend/src/prior.json` is deployed with the code.

```
cd backend
sam build
sam deploy --guided      # stack: dihadi, region: ap-south-1, AlertEmail: yours
```

Paste the `ApiBase` output into `DEPLOYED_API` in `frontend/index.html`, `frontend/fund.html` and `frontend/member.html`, commit and push. In the Amplify console choose Deploy an app and connect this repository; `amplify.yml` serves the `frontend` folder. To try the daily job, run MonitorFunction from the Lambda console with `{}`.

## Tests

```
cd backend
python -m unittest discover -s tests -v
```

22 tests: the exact distribution against theory, the GLM recovering a known slope, credibility weights, event detection and tiers, the yearly cap, a heatwave spanning two checks being paid once and upgraded, the daily monitor, Hindi and English member text, and Polly. AWS is mocked with moto and weather with a fixed series.

## Limits

- ERA5 is a 25 km grid and runs a little cooler than city streets, so a station-based trigger like 43.6 °C may pay rarely. The page shows how often the trigger would have paid recently and suggests one that pays about 1–2 times a year.
- The link to warming uses global temperature only (pattern scaling). Local factors such as aerosols and urban growth are not modelled.
- Basis risk is not modelled: a member can lose a day when the cover doesn't pay, or the reverse.
- Fund links are unguessable but not password protected.
- This is a technical price for comparison, not a licensed insurance product.

## Licence

MIT

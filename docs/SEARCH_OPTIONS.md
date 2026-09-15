# hiring.cafe `searchState` reference

Extracted from hiring.cafe's frontend bundle (September 2026). These are the
keys the site itself serializes into `?searchState=` and the exact option
strings it uses. `python -m jobFilter validate` checks a config against this
list; the machine-readable copy is `jobFilter/schema.py`. Template: `setup/search.template.json`.

A key that is omitted takes the site default (shown in parentheses).

## Core

| Key | Type | Options / notes |
|---|---|---|
| `searchQuery` | string | free text ("") |
| `jobTitleQuery`, `jobDescriptionQuery`, `technologyKeywordsQuery`, `requirementsKeywordsQuery` | string | text that must appear in that field |
| `workplaceTypes` | list | `Remote`, `Hybrid`, `Onsite`, `Field` (all) |
| `commitmentTypes` | list | `Full Time`, `Part Time`, `Contract`, `Internship`, `Temporary`, `Seasonal`, `Volunteer` (all) |
| `seniorityLevel` | list | `No Prior Experience Required`, `Entry Level`, `Mid Level`, `Senior Level` (all) |
| `roleTypes` | list | `Individual Contributor`, `People Manager` |
| `dateFetchedPastNDays` | number or `{from,to}` | days since hiring.cafe fetched the job (121). UI presets: 1 today, 3, 7, 14, 30. Custom range `{"from":"YYYY-MM-DD","to":"YYYY-MM-DD"}` |
| `sortBy` | string | `default` relevance, `date` newest, `date_asc`, `compensation_desc`, `compensation_asc`, `experience_asc`, `experience_desc`; `resume_match_desc`, `personalization_desc` need login |
| `roleYoeRange` | `[min,max]` | years of role experience ([0,20]; 20 = 20+) |
| `excludeIfRoleYoeIsNotSpecified` | bool | (false) |
| `managementYoeRange`, `excludeIfManagementYoeIsNotSpecified` | same | management experience |
| `securityClearances` | list | `None`, `Confidential`, `Secret`, `Top Secret`, `Top Secret/SCI`, `Public Trust`, `Interim Clearances`, `Other` |
| `applicationFormEase` | list | `Simple`, `Time Consuming` |
| `benefitsAndPerks` | list | `Generous Paid Time Off`, `Four Day Work Week`, `401k Matching`, `Generous Parental Leave`, `Retirement Plan`, `Tuition Reimbursement`, `Visa Sponsorship`, `Relocation Assistance` |
| `encouragedToApply` | list | `Military Veterans`, `Fair Chance` |

## Location

`locations` and `excludedWorkplaceLocations` are lists of place objects. Get one
from `https://hiringcafe.com/api/searchLocation?query=<text>` and use the
`placeDetail` object, optionally adding `options`:

```json
{
  "id": "IBk1yZQBoEtHp_8UuN4b",
  "formatted_address": "Nyack, NY, US",
  "types": ["locality"],
  "address_components": [
    {"long_name": "Nyack", "short_name": "Nyack", "types": ["locality"]},
    {"long_name": "New York", "short_name": "NY", "types": ["administrative_area_level_1"]},
    {"long_name": "United States", "short_name": "US", "types": ["country"]}
  ],
  "geometry": {"location": {"lat": 41.09065, "lon": -73.91791}},
  "options": {"flexible_regions": ["anywhere_in_continent", "anywhere_in_world"]}
}
```

The site's built-in country object uses `"id": "user_country"` and
`geometry.location` of `{lat: 0, lon: 0}` (see the template). `flexible_regions`
controls whether "Remote anywhere in North America / worldwide" jobs count.
`defaultToUserLocation` (true) makes the site add your IP country when
`locations` is empty.

## Company

| Key | Type | Options / notes |
|---|---|---|
| `companyNames`, `excludedCompanyNames` | list | company display names |
| `companyKeywords`, `excludedCompanyKeywords` | list | free text; `companyKeywordsBooleanOperator` `OR`/`AND` |
| `industries`, `excludedIndustries`, `departments` | list | the site's labels |
| `companyHqCountries`, `excludedCompanyHqCountries` | list | country names |
| `organizationTypes`, `excludedOrganizationTypes` | list | `private`, `public`, `non-profit`, `government`, `cooperative / mutual` |
| `companyPublicOrPrivate` | string | `all` (default); public/private |
| `isNonProfit` | string | `all` (default) |
| `companySizeRanges` | list of `[min,max]` | headcount buckets |
| `minYearFounded`, `maxYearFounded` | int | |
| `latestInvestmentSeries`, `excludedLatestInvestmentSeries` | list | `angel`, `pre-seed`, `seed`, `series a` ... `series i`, `crowdfunding`, `private equity`, `convertible note`, `debt financing`, `secondary market`, `grant`, `corporate round`, `initial coin offering (ico)`, `post-ipo equity`, `post-ipo debt`, `post-ipo secondary`, `non-equity assistance`, `funding round` |
| `latestInvestmentYearRange` | `[min,max]` | |
| `latestInvestmentAmount`, `latestInvestmentCurrency`, `investors`, `excludedInvestors` | | |
| `usaGovPref` | | US government preference flag (null) |

## Compensation

`currency` and `frequency` are `{"label": ..., "value": ...}` objects (`value`
null = Any). `frequency.value`: `Hourly`, `Daily`, `Weekly`, `Bi-Weekly`,
`Monthly`, `Yearly`. `calcFrequency` (`Yearly`) is the unit for
`minCompensationLowEnd`, `minCompensationHighEnd`, `maxCompensationLowEnd`,
`maxCompensationHighEnd`. `restrictJobsToTransparentSalaries` true drops jobs
without a listed salary.

## Education, licenses, languages

- `associatesDegreeRequirements`, `bachelorsDegreeRequirements`,
  `mastersDegreeRequirements`, `doctorateDegreeRequirements`: list of
  `Required`, `Preferred`, `Not Mentioned`.
- `*DegreeFieldsOfStudy` / `excluded*DegreeFieldsOfStudy`: lists of field names.
- `licensesAndCertifications`, `excludedLicensesAndCertifications`: lists;
  `excludeAllLicensesAndCertifications` bool.
- `languageRequirements`, `excludedLanguageRequirements`: lists;
  `languageRequirementsOperator` `OR`/`AND`;
  `excludeJobsWithAdditionalLanguageRequirements` bool.

## Work conditions

| Key | Options |
|---|---|
| `physicalEnvironments` | `Office`, `Outdoor`, `Vehicle`, `Industrial`, `Customer-Facing` |
| `physicalLaborIntensity`, `oralCommunicationLevels`, `computerUsageLevels`, `cognitiveDemandLevels` | `Low`, `Medium`, `High` |
| `physicalPositions` | `Sitting`, `Standing` |
| `airTravelRequirement`, `landTravelRequirement` | `None`, `Minimal`, `Moderate`, `Extensive` |
| `morningShiftWork`, `eveningShiftWork`, `overnightShiftWork` | `Required`, `Optional`, `Not Indicated` |
| `onCallRequirements` | `None`, `Occasional (once a month or less)`, `Regular (once a week or more)` |
| `weekendAvailabilityRequired`, `holidayAvailabilityRequired`, `overtimeRequired` | `Doesn't Matter` (default), yes/no |

## Account-only (ignored without login)

`hideJobTypes` (`Hidden`, `Saved`, `Applied`, `Viewed`), `degenMode`,
`degenModeCategories`, `activitySignals`, `restrictedSearchAttributes`.

## Result fields worth knowing

Each hit's `v5_processed_job_data` includes `job_category`,
`min_industry_and_role_yoe`, `security_clearance`, `visa_sponsorship`,
`workplace_countries`, `formatted_workplace_location`, `estimated_publish_date`,
`yearly_min_compensation` / `yearly_max_compensation`, `technical_tools`,
`requirements_summary`. Local `rules` in the config filter on these.

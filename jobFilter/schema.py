"""Valid hiring.cafe `searchState` keys and option values.

Extracted from hiring.cafe's own JS bundle (module constants + `saveSearchKeys`
+ `initialState`), September 2026. `validate()` flags unknown keys and
out-of-list values before a search is sent.
"""
from __future__ import annotations

from typing import Any

# Every key hiring.cafe serializes into the URL's searchState.
SAVE_SEARCH_KEYS: list[str] = [
    "locations", "excludedWorkplaceLocations", "searchQuery", "workplaceTypes", "commitmentTypes",
    "applicationFormEase", "dateFetchedPastNDays", "currency", "frequency", "minCompensationHighEnd",
    "restrictJobsToTransparentSalaries", "hideJobTypes", "industries", "excludedIndustries",
    "companyKeywords", "excludedCompanyKeywords", "departments", "roleYoeRange",
    "excludeIfRoleYoeIsNotSpecified", "managementYoeRange", "excludeIfManagementYoeIsNotSpecified",
    "associatesDegreeFieldsOfStudy", "excludedAssociatesDegreeFieldsOfStudy",
    "bachelorsDegreeFieldsOfStudy", "excludedBachelorsDegreeFieldsOfStudy",
    "mastersDegreeFieldsOfStudy", "excludedMastersDegreeFieldsOfStudy",
    "doctorateDegreeFieldsOfStudy", "excludedDoctorateDegreeFieldsOfStudy", "companyNames",
    "excludedCompanyNames", "companyHqCountries", "excludedCompanyHqCountries",
    "defaultToUserLocation", "roleTypes", "restrictedSearchAttributes", "usaGovPref", "calcFrequency",
    "minCompensationLowEnd", "maxCompensationLowEnd", "maxCompensationHighEnd",
    "physicalEnvironments", "physicalLaborIntensity", "physicalPositions", "oralCommunicationLevels",
    "computerUsageLevels", "cognitiveDemandLevels", "associatesDegreeRequirements",
    "bachelorsDegreeRequirements", "mastersDegreeRequirements", "doctorateDegreeRequirements",
    "licensesAndCertifications", "excludedLicensesAndCertifications",
    "excludeAllLicensesAndCertifications", "seniorityLevel", "securityClearances",
    "languageRequirements", "excludedLanguageRequirements", "languageRequirementsOperator",
    "excludeJobsWithAdditionalLanguageRequirements", "airTravelRequirement", "landTravelRequirement",
    "morningShiftWork", "eveningShiftWork", "overnightShiftWork", "weekendAvailabilityRequired",
    "holidayAvailabilityRequired", "overtimeRequired", "onCallRequirements", "benefitsAndPerks",
    "companyKeywordsBooleanOperator", "encouragedToApply", "sortBy", "jobTitleQuery",
    "technologyKeywordsQuery", "jobDescriptionQuery", "requirementsKeywordsQuery",
    "companyPublicOrPrivate", "latestInvestmentYearRange", "latestInvestmentSeries",
    "latestInvestmentAmount", "latestInvestmentCurrency", "investors", "excludedInvestors",
    "isNonProfit", "organizationTypes", "excludedOrganizationTypes", "companySizeRanges",
    "minYearFounded", "maxYearFounded", "excludedLatestInvestmentSeries", "degenMode",
    "degenModeCategories", "activitySignals",
]

# Keys whose values must come from a fixed list (list-valued keys take a subset).
ENUMS: dict[str, list[Any]] = {
    "workplaceTypes": ["Remote", "Hybrid", "Onsite", "Field"],
    "commitmentTypes": ["Full Time", "Part Time", "Contract", "Internship", "Temporary", "Seasonal", "Volunteer"],
    "seniorityLevel": ["No Prior Experience Required", "Entry Level", "Mid Level", "Senior Level"],
    "roleTypes": ["Individual Contributor", "People Manager"],
    "securityClearances": ["None", "Confidential", "Secret", "Top Secret", "Top Secret/SCI", "Public Trust", "Interim Clearances", "Other"],
    "applicationFormEase": ["Simple", "Time Consuming"],
    "physicalEnvironments": ["Office", "Outdoor", "Vehicle", "Industrial", "Customer-Facing"],
    "physicalLaborIntensity": ["Low", "Medium", "High"],
    "physicalPositions": ["Sitting", "Standing"],
    "oralCommunicationLevels": ["Low", "Medium", "High"],
    "computerUsageLevels": ["Low", "Medium", "High"],
    "cognitiveDemandLevels": ["Low", "Medium", "High"],
    "airTravelRequirement": ["None", "Minimal", "Moderate", "Extensive"],
    "landTravelRequirement": ["None", "Minimal", "Moderate", "Extensive"],
    "morningShiftWork": ["Required", "Optional", "Not Indicated"],
    "eveningShiftWork": ["Required", "Optional", "Not Indicated"],
    "overnightShiftWork": ["Required", "Optional", "Not Indicated"],
    "onCallRequirements": ["None", "Occasional (once a month or less)", "Regular (once a week or more)"],
    "weekendAvailabilityRequired": ["Doesn't Matter", "Yes", "No"],
    "holidayAvailabilityRequired": ["Doesn't Matter", "Yes", "No"],
    "overtimeRequired": ["Doesn't Matter", "Yes", "No"],
    "associatesDegreeRequirements": ["Required", "Preferred", "Not Mentioned"],
    "bachelorsDegreeRequirements": ["Required", "Preferred", "Not Mentioned"],
    "mastersDegreeRequirements": ["Required", "Preferred", "Not Mentioned"],
    "doctorateDegreeRequirements": ["Required", "Preferred", "Not Mentioned"],
    "benefitsAndPerks": ["Generous Paid Time Off", "Four Day Work Week", "401k Matching", "Generous Parental Leave", "Retirement Plan", "Tuition Reimbursement", "Visa Sponsorship", "Relocation Assistance"],
    "encouragedToApply": ["Military Veterans", "Fair Chance"],
    "hideJobTypes": ["Hidden", "Saved", "Applied", "Viewed"],
    "organizationTypes": ["private", "public", "non-profit", "government", "cooperative / mutual"],
    "excludedOrganizationTypes": ["private", "public", "non-profit", "government", "cooperative / mutual"],
    "latestInvestmentSeries": ["angel", "pre-seed", "seed", "series a", "series b", "series c", "series d", "series e", "series f", "series g", "series h", "series i", "crowdfunding", "private equity", "convertible note", "debt financing", "secondary market", "grant", "corporate round", "initial coin offering (ico)", "post-ipo equity", "post-ipo debt", "post-ipo secondary", "non-equity assistance", "funding round"],
    "excludedLatestInvestmentSeries": ["angel", "pre-seed", "seed", "series a", "series b", "series c", "series d", "series e", "series f", "series g", "series h", "series i", "crowdfunding", "private equity", "convertible note", "debt financing", "secondary market", "grant", "corporate round", "initial coin offering (ico)", "post-ipo equity", "post-ipo debt", "post-ipo secondary", "non-equity assistance", "funding round"],
    "sortBy": ["default", "date", "date_asc", "compensation_desc", "compensation_asc", "experience_asc", "experience_desc", "resume_match_desc", "personalization_desc"],
    "calcFrequency": ["Hourly", "Daily", "Weekly", "Bi-Weekly", "Monthly", "Yearly"],
    "languageRequirementsOperator": ["OR", "AND"],
    "companyKeywordsBooleanOperator": ["OR", "AND"],
    "companyPublicOrPrivate": ["all", "public", "private"],
    "isNonProfit": ["all", "yes", "no"],
}

# Keys with the "Doesn't Matter"/"Yes"/"No" and "all" enums above were inferred
# from defaults rather than an explicit list in the bundle; validate() treats
# them as warnings, not errors.
SOFT_ENUMS = {"weekendAvailabilityRequired", "holidayAvailabilityRequired", "overtimeRequired",
              "companyPublicOrPrivate", "isNonProfit", "languageRequirementsOperator",
              "companyKeywordsBooleanOperator"}

DEFAULT_DATE_FETCHED_PAST_N_DAYS = 121  # hiring.cafe default when the key is absent


def validate(state: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) for a searchState dict."""
    errors: list[str] = []
    warnings: list[str] = []
    for key, value in state.items():
        if key.startswith("_"):
            continue  # comment keys in the template
        if key not in SAVE_SEARCH_KEYS:
            errors.append(f"unknown key '{key}'")
            continue
        if key in ENUMS:
            allowed = ENUMS[key]
            values = value if isinstance(value, list) else [value]
            bad = [v for v in values if v not in allowed]
            if bad:
                msg = f"{key}: {bad} not in {allowed}"
                (warnings if key in SOFT_ENUMS else errors).append(msg)
    d = state.get("dateFetchedPastNDays")
    if d is not None and not (isinstance(d, (int, float)) or (isinstance(d, dict) and ("from" in d or "to" in d))):
        errors.append("dateFetchedPastNDays must be a number of days or {\"from\": \"YYYY-MM-DD\", \"to\": \"YYYY-MM-DD\"}")
    for rng in ("roleYoeRange", "managementYoeRange"):
        v = state.get(rng)
        if v is not None and not (isinstance(v, list) and len(v) == 2):
            errors.append(f"{rng} must be [min, max]")
    for loc in state.get("locations") or []:
        if not isinstance(loc, dict) or "address_components" not in loc or "formatted_address" not in loc:
            errors.append("each location must be a placeDetail object from /api/searchLocation (see template)")
            break
    return errors, warnings


def strip_comments(state: dict[str, Any]) -> dict[str, Any]:
    """Drop `_comment`-style keys so a template can be used directly."""
    return {k: v for k, v in state.items() if not k.startswith("_")}

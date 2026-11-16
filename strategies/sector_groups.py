"""
Curated NSE sector ETF groups: sector name -> member ticker symbols.

Auto-derived from NSE's ETF category text (keyword matching), then reviewed.
Only "sectoral" ETFs are included here - broad-market (Nifty 50/Sensex/Next 50/
Smallcap/Midcap), factor/smart-beta (Momentum/Quality/Value/LowVol/Alpha/Equal
Weight), gold/silver, gilt/debt/liquid, and international ETFs are deliberately
excluded since they aren't sector plays.

Edit this dict directly to add/remove ETFs or whole sectors as new ones list.
"""

SECTOR_GROUPS = {
    "Banking (broad)": [
        "BANKBEES", "PVTBANIETF", "BANKNIFTY1", "ABSLBANETF", "BANKBETA",
        "SETFNIFBK", "BANKIETF", "HDFCNIFBAN", "BANKETF", "BANKPSU",
        "BNKETFAXIS", "SBIETFPB", "BANKBETF", "BANKADD", "MOBANK10",
        "BANK10ADD", "EBANKNIFTY", "NPBET", "ABSL10BANK", "BBNPNBETF",
    ],
    "Private Banks": ["HDFCPVTBAN", "PVTBANKADD", "PVTBKGROWW"],
    "PSU Banks": ["PSUBNKBEES", "PSUBNKIETF", "PSUBANK", "HDFCPSUBK", "PSUBANKADD", "SBIBPB", "GROWWPSUBK"],
    "Financial Services (ex-bank)": ["MOCAPITAL", "FINIETF", "BFSI", "GROWWCAPM", "ECAPINSURE"],
    "IT / Technology": ["ITBEES", "ITETF", "IT", "ITIETF", "HDFCNIFIT", "SBIETFIT", "TECH", "TNIDETF", "ITADD", "ITBETA", "ITAXIS"],
    "Pharma / Healthcare": ["PHARMABEES", "HEALTHIETF", "GROWWHOSPI", "HEALTHADD", "HEALTHY", "MOHEALTH", "HEALTHCARE", "HEALTHAXIS"],
    "FMCG / Consumption": ["FMCGIETF", "CONSUMBEES", "CONSUMER", "CONSUMIETF", "CONSUMAXIS", "CONS", "FMCGADD", "SBIETFCON"],
    "Auto / EV": ["AUTOBEES", "AUTOIETF", "GROWWEV", "EVINDIA", "EVIETF"],
    "Energy / Oil & Gas / Power": ["GROWWPOWER", "ENERGY", "OILIETF", "MOENERGY"],
    "Metal / Mining": ["METALIETF", "METAL", "GROWWMETAL"],
    "Infrastructure / PSE": ["INFRAIETF", "INFRABEES", "ABSLPSE", "INFRA", "MOINFRA", "GROWWPSE", "MOPSE"],
    "PSU (broad)": ["ICICIB22", "GROWWRAIL"],
    "Realty": ["MOREALTY"],
    "Defence": ["MODEFENCE", "GROWWDEFNC", "DEFENCE"],
    "Chemicals": ["CHEMICAL", "GROWWCHEM"],
    "Manufacturing": ["MAKEINDIA", "MANUFGBEES", "MOMGF"],
    "MNC": ["MNC", "MOMNC"],
    "Services (broad)": ["MOSERVICE"],
    "Tourism": ["MOTOUR"],
    "Commodities (equity)": ["COMMOIETF"],
}


# Maps each Sector Scanner sector (strategies.sector_scanner.SECTORS) to the
# SECTOR_GROUPS key whose index ETF best proxies it, for benchmarking relative
# strength against the sector instead of Nifty 50. None = no clean sector ETF
# exists (RS-vs-sector is left blank for that sector rather than forced onto a
# poor proxy). Edit freely as ETFs/sectors are added.
SCANNER_SECTOR_ETF_GROUP = {
    "Small Finance Banks": "Banking (broad)",
    "Public Sector Banks": "PSU Banks",
    "Private Banks": "Private Banks",
    "NBFCs": "Financial Services (ex-bank)",
    "Housing Finance": "Financial Services (ex-bank)",
    "Insurance": "Financial Services (ex-bank)",
    "Capital Markets": "Financial Services (ex-bank)",
    "IT Services & Software": "IT / Technology",
    "Pharma": "Pharma / Healthcare",
    "FMCG": "FMCG / Consumption",
    "Auto": "Auto / EV",
    "Metals": "Metal / Mining",
    "Realty": "Realty",
    "Energy": "Energy / Oil & Gas / Power",
    "Telecom": None,
    "Infrastructure": "Infrastructure / PSE",
    "Capital Goods": "Manufacturing",
    "Cement & Building Materials": None,
    "Chemicals": "Chemicals",
    "Fertilisers & Agrochemicals": "Chemicals",
    "Paper & Packaging": None,
    "Textiles": None,
    "Consumer Durables": "FMCG / Consumption",
    "Retail & Consumer Discretionary": "FMCG / Consumption",
    "Hotels, Travel & Aviation": "Tourism",
    "Healthcare Services": "Pharma / Healthcare",
    "Logistics & Shipping": None,
    "Media & Entertainment": None,
    "Defence & Shipbuilding": "Defence",
    "Electronics (EMS)": "Manufacturing",
    "Sugar & Agri": None,
    "Internet & New-age": None,
}

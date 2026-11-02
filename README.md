# Nifty 50 Scanner

**Website:** https://bishaltureha.github.io/nifty-50-day-trader/

- Nifty 50 ke saare 50 shares ki list, har share ke saath uska setup (20-din high, bull flag, average ke upar, oversold)
- Kisi share par click karo: chart, setup kyun bana, ahem levels, RSI aur position-size calculator
- Sabse upar Nifty 50 index
- [Guide aur shabdkosh](https://bishaltureha.github.io/nifty-50-day-trader/guide.html)

## Data kaise update hota hai

`.github/workflows/update-data.yml` har weekday 8:30, 16:00 aur 19:30 IST par
`scripts/build_data.py` chalata hai. Ye `scanner/scan.py` (trader-scanner, NSE/IST ke liye) se
Yahoo Finance ka data laata hai aur `data.json` likhta hai. Haath se chalana ho:
Actions tab, "Update scanner data", "Run workflow".

`scanner/scan.py` [day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill) se hai (MIT, `scanner/LICENSE`).

**Financial advice nahi hai.** Day trading aur options mein zyadatar log paisa gawaate hain.

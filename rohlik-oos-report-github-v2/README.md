# Rohlik OOS report — GitHub Actions

Denní availability report Rohlik.cz (POS Media letterhead), stejná logika jako skill
`rohlik-oos-report`: 3 stavy (in stock / out of stock / not listed) proti uložené footprint
baseline, nikdy keyword search, low stock ≤ 5 ks. Layout je styl po stylu převzatý ze
schváleného Polaris reportu z 25. 9. 2026 a generuje ho kód, takže je každý den identický.

## Jak to funguje

1. GitHub Actions každý den v 05:30 CET / 06:30 CEST projde všechny `config/*.yml` (dnes Polaris
   a Milko) a pro každou značku spustí `python -m oos.run --client <name>` — postupně, ne paralelně,
   protože všechny sdílí jeden Rohlík účet. Selhání jedné značky nezastaví ostatní, ale běh skončí
   červeně a přijde ti e-mail.
2. Skript **nepoužívá žádné přihlášení ani MCP**. Pro každou zónu otevře novou anonymní session na
   rohlik.cz a zvolí doručovací adresu stejně jako web (našeptávač → confirm → check), pak čte
   veřejné endpointy `/api/v1/products/stock` a `/prices`. Žádná hesla, žádný účet, žádné limity MCP.
3. Ověření skladu: každý řádek stocku nese `warehouseId` a musí se shodovat se skladem, který
   Rohlík přidělil adrese zóny (a s `expected_warehouse_id` v configu, je-li vyplněn). Dvě zóny
   nesmí skončit ve stejném skladu. Jinak je zóna FAILED a z počtů vyřazena.
4. Vygeneruje HTML, spustí 13 self-checků ze specu (logo fingerprint, type scale, integrita
   klasifikace…). Když cokoliv neprojde, report se **nepublikuje** a GitHub ti pošle e-mail o failu.
5. Report se uloží do `reports/polaris/` (+ `latest.html`) a jako stažitelný artefakt běhu.
   Stav (dny v OOS pro stale flag, in-stock počty pro detekci anomálií) jde do `state/`.

Rozdíl proti skillu: přepnutí skladu se primárně ověřuje adresou z checkoutu, canary SKU se jen
zaznamenává. Důvod: při testu 10. 11. měl canary 1423451 v Počernicích, Brně i Ostravě shodně
6 ks / saleId null, takže podle canary pravidla by Brno i Ostrava falešně spadly jako FAILED.
Když `get_checkout` nejde přečíst, skript se vrátí k původnímu canary pravidlu.

## Nastavení (jednorázově, ~10 min)

1. Na GitHubu vytvoř **privátní** repo (např. `rohlik-oos-report`) a nahraj obsah tohoto zipu.
2. *Settings → Secrets and variables → Actions → New repository secret*:
   - `RHL_EMAIL` — e-mail Rohlík účtu, který má uložených 5 zónových adres
   - `RHL_PASS` — heslo k tomuto účtu
3. *Actions → Rohlik OOS daily report → Run workflow* (první ruční běh).
4. Při prvním přihlášení z nové IP může Rohlík poslat **e-mail s potvrzením nového přihlášení**.
   Potvrď ho a spusť workflow znovu.
5. Hotový report: záložka běhu → *Artifacts*, nebo soubor `reports/polaris/latest.html` v repu.

## Nová značka

Zkopíruj `config/polaris.yml` → `config/<brand>.yml`, vyplň SKU a `not_listed_in` z onboardingu
(baseline se pořád zakládá s Claudem, nikdy ne odhadem). Nic dalšího — denní běh si nový soubor
najde sám. Ručně jen jednu značku: *Run workflow* → `client=<brand>`.

Milko footprint je zatím jen „user-stated" (25. 9.), neověřený screenshotem po skladech — report to
uvádí v Method. Stojí za to ho při příležitosti ověřit.

## Refresh baseline

Uprav `not_listed_in` v configu a datum `footprint.established`. Další běh změnu sám zapíše do
„Listing changes since last run".

## Lokální test bez Rohlíku

```
pip install -r requirements.txt
python tests/mock_rohlik_mcp.py &      # mock se skutečnými daty změřenými 10. 11. 2026
ROHLIK_MCP_URL=http://127.0.0.1:8765/mcp python -m oos.run --client polaris
```
`MOCK_BREAK_ZONE=5530325` nasimuluje neověřitelné Brno (zóna musí vyjít jako FAILED).

## Známá rizika

- **Podmínky Rohlíku:** MCP služba je určena pro běžné zákaznické užití a jiné použití vyžaduje
  předchozí písemný souhlas Rohlíku. To platí i pro dnešní běhy přes Clauda, automatizace to jen
  zviditelňuje. Doporučuji si souhlas vyžádat přes partnerský kontakt.
- **Sdílený účet:** běh přepíná doručovací adresu účtu. Nespouštěj současně Claude-based report
  na stejném účtu (workflow sám sebe hlídá přes `concurrency`, Clauda ne).
- **Legacy přihlášení heslem** může Rohlík časem vypnout ve prospěch OAuth. Pak spadne krok
  „Measure" s jasnou chybou a e-mailem od GitHubu.
- GitHub cron může začít o pár minut později; neaktivní repo (60 dní bez commitu) GitHub
  pozastaví, ale denní commit reportu tomu brání.

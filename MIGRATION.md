# Entity Renamer 5.0.0 — migration draft / szkic migracji

**Unreleased candidate; final HACS installation and migration are pending R11.B qualification.** Do not treat these steps as a published release or a claim of automatic HACS recategorization. Public 4.6.1 is a Dashboard plugin; 4.6.2 is a separately prepared transition plugin, not a prerequisite. The candidate is one Integration package containing the full card and backend. The root compatibility JavaScript keeps the 4.6.1 workflow and does not provide the backend or full candidate card.

## Before any change

Make and verify a full Home Assistant backup, including dashboards, configuration and `.storage`, and know how to restore it. Note the current card configuration and resources. Review version and family restrictions in [README](README.md). The candidate has been tested on selected versions and data, not every configuration. A reload can interrupt running automations and scripts. Avoid a migration while an operation is running or its result is unknown; never replay UNKNOWN. Use journal inspection and a newly admitted, separately confirmed restore where available.

## Fresh installation (draft for the future release)

1. In HACS, add `https://github.com/MacSiem/ha-entity-renamer` as a custom repository in **Integration**, select the released 5.0.0 package when available and download it. Do not select the Dashboard compatibility file as the integration.
2. Restart Home Assistant. In **Settings → Devices & services → Add integration**, choose **Entity Renamer** and complete the config flow once. Refresh the browser. Add the `custom:ha-entity-renamer` card using the dashboard card picker, or use that type in YAML.
3. Check that the integration is loaded, the full card opens and only the intended card resource is present. If it does not, stop and inspect HA logs and resources before trying changes.

## Existing 4.6.1 or bridge 4.6.2

1. Back up and record the existing configuration as above. The optional 4.6.1 → 4.6.2 Dashboard update keeps the legacy workflow and displays a migration notice; it does not migrate or uninstall anything. The same migration is intended directly from 4.6.1 if the bridge is skipped.
2. When 5.0.0 is released, update the existing Dashboard entry to its 5.0.0 compatibility card and reload the browser. Open your existing Entity Renamer card and its migration information. As an administrator, wait for all HACS downloads and updates to finish and do not start another during this step. Choose **Prepare migration in HACS**, read the confirmation and confirm only after verifying your backup. This changes the HACS repository entry to Integration; it does not uninstall the old card, delete its files or resources, rename anything or remove history. Wait for the ready message. The step requires HACS 2.0.5 and a connected administrator session; HACS does not provide an atomic lock against other clients. If the result cannot be confirmed, stop: inspect HACS and do not repeat the operation blindly, including from another browser. The attempt marker is stored in this browser, so clearing browser data is not a safe retry. Once ready, open HACS, download Entity Renamer as **Integration**, restart Home Assistant, add it through the config flow and refresh the browser.
3. In storage-mode Lovelace, exactly one recognized old resource (`/hacsfiles/ha-entity-renamer/ha-entity-renamer.js` or `/local/community/ha-entity-renamer/ha-entity-renamer.js`) is updated in place to the managed integration URL. Multiple recognized copies or another unrecognized URL with the same filename stop setup for manual review; no foreign resource is removed. Existing dashboard card configuration is retained.
4. With YAML Lovelace resources, the integration never edits YAML. If the old recognized resource is present, setup waits instead of loading a second copy. After backup, deliberately remove only your old Entity Renamer resource entry from the YAML you own, leave other resources intact, validate the configuration and restart HA. The integration can then register its versioned card through Home Assistant's managed extra JS URL. Do not add a new manually versioned entry on every update. If you intentionally keep a YAML entry, it must match the current integration URL exactly, and you must maintain it yourself. Resolve ambiguous resources manually before retrying.
5. Refresh the browser (hard refresh if it still serves an old card), inspect the resource and card, and verify the intended account's history and settings. Queued proposals are in memory and are not promised across a reload. No automatic uninstall, foreign configuration edits or deletion of recovery data is part of the migration.

## Recovery and rollback

Stop on error or UNKNOWN; inspect the journal and affected registries before deciding what happened. Never repeat an uncertain write or apply an inverse without fresh admission. The local transaction backups are not a full HA backup. If rollback of the installation is necessary, use your verified HA backup and restore plan, including the prior plugin/resource and dashboard configuration; inspect the state first and avoid overwriting changes made since the backup. Do not delete journal, preparations or receipts as a shortcut. A native restore may be unavailable for an uncertain scope.

**Evidence boundary:** synthetic native Core tests established fresh integration setup, single runtime/resource, preservation and recognized storage takeover; they also established that an old YAML resource causes safe setup retry. The final published HACS fresh/upgrade/skip-bridge path, pending-update UI, rollback and public propagation remain R11.B work. No release is available yet from this branch.

# Polski

**Niewydany kandydat 5.0.0; końcowy przebieg instalacji i migracji HACS czeka na kwalifikację R11.B.** Publiczne 4.6.1 jest pluginem Dashboard. Przygotowany osobno bridge 4.6.2 nie jest wymagany. Kandydat jest jednym pakietem Integration z pełną kartą i backendem; główny plik JS repozytorium jest kartą zgodności z działaniem 4.6.1, nie instaluje backendu.

## Przygotowanie

Wykonaj i sprawdź pełną kopię Home Assistant, także dashboardów, konfiguracji i `.storage`, oraz przygotuj sposób jej przywrócenia. Zapisz dotychczasową konfigurację karty i zasobów. Sprawdź ograniczenia wersji i rodzin w [README](README.md). Testy obejmują wybrane wersje i dane, nie każdą konfigurację. Reload może przerwać działające automatyzacje i skrypty. Nie migruj podczas operacji ani przy nieznanym wyniku; UNKNOWN nie ponawiaj. Sprawdź dziennik; przywracanie wymaga nowej kwalifikacji i osobnego potwierdzenia.

## Nowa instalacja — szkic na przyszłe wydanie

1. W HACS dodaj `https://github.com/MacSiem/ha-entity-renamer` jako repozytorium własne kategorii **Integration**, wybierz opublikowany pakiet 5.0.0, gdy będzie dostępny, i pobierz go. Plik zgodności Dashboard nie instaluje integracji.
2. Uruchom ponownie Home Assistant. W **Ustawienia → Urządzenia i usługi → Dodaj integrację** wybierz **Entity Renamer** i jednokrotnie przejdź konfigurację. Odśwież przeglądarkę i dodaj kartę przez edytor albo typ `custom:ha-entity-renamer` w YAML.
3. Sprawdź załadowanie integracji, pełną kartę i pojedynczy właściwy zasób. Przy błędzie zatrzymaj się i sprawdź logi oraz zasoby.

## Z 4.6.1 lub bridge 4.6.2

1. Przygotuj kopię i zapis konfiguracji. Opcjonalna aktualizacja Dashboard 4.6.1 → 4.6.2 zachowuje dotychczasową funkcję i pokazuje komunikat; niczego sama nie migruje ani nie odinstalowuje. Możliwa jest też planowana ścieżka bez bridge z 4.6.1.
2. Po wydaniu 5.0.0 zaktualizuj istniejący wpis Dashboard do karty zgodności 5.0.0 i odśwież przeglądarkę. Otwórz dotychczasową kartę Entity Renamer i informację o migracji. Jako administrator poczekaj na koniec wszystkich pobierań i aktualizacji HACS; nie uruchamiaj innych w trakcie. Wybierz **Przygotuj migrację w HACS**, przeczytaj potwierdzenie i potwierdź dopiero po sprawdzeniu kopii. Krok przełącza wpis repozytorium HACS na Integration; nie odinstalowuje starej karty, nie usuwa jej plików, zasobów, historii ani danych HA i nie zmienia nazw. Poczekaj na komunikat gotowości. Wymagane są HACS 2.0.5 i aktywna sesja administratora; HACS nie zapewnia atomowej blokady wobec innych klientów. Jeśli nie można potwierdzić wyniku, zatrzymaj się: sprawdź HACS i nie ponawiaj operacji w ciemno, również w innej przeglądarce. Znacznik próby jest w tej przeglądarce; usunięcie jej danych nie jest bezpiecznym ponowieniem. Po komunikacie gotowości otwórz HACS, pobierz Entity Renamer jako **Integration**, zrestartuj HA, dodaj integrację w konfiguracji i odśwież przeglądarkę.
3. W trybie storage dokładnie jeden rozpoznany stary zasób (`/hacsfiles/ha-entity-renamer/ha-entity-renamer.js` albo `/local/community/ha-entity-renamer/ha-entity-renamer.js`) jest aktualizowany w miejscu do zarządzanego URL. Duplikaty lub nierozpoznany URL o tej samej nazwie zatrzymują konfigurację do ręcznej analizy; cudze zasoby nie są usuwane. Konfiguracja karty dashboardu pozostaje zachowana.
4. Przy zasobach Lovelace w YAML integracja nie edytuje pliku. Stary rozpoznany wpis wstrzymuje konfigurację zamiast załadowania drugiej kopii. Po wykonaniu kopii świadomie usuń wyłącznie własny stary wpis zasobu Entity Renamer ze swojego YAML, zachowaj pozostałe, sprawdź konfigurację i zrestartuj HA. Integracja może wtedy zarejestrować wersjonowaną kartę przez zarządzany extra JS URL. Nie dodawaj ręcznie wersji zasobu po każdej aktualizacji. Jeśli celowo utrzymujesz wpis YAML, musi dokładnie odpowiadać aktualnemu URL integracji i wymaga samodzielnej aktualizacji. Niejednoznaczne zasoby rozstrzygnij ręcznie przed ponowieniem konfiguracji.
5. Odśwież przeglądarkę, w razie potrzeby wymuś odświeżenie, sprawdź zasób, kartę, historię i ustawienia właściwego konta. Kolejka jest w pamięci i może zniknąć po przeładowaniu. Migracja nie odinstalowuje nic automatycznie, nie edytuje cudzej konfiguracji ani nie usuwa danych odzyskiwania.

## Błąd i powrót

Przy błędzie lub UNKNOWN zatrzymaj się; sprawdź dziennik i odpowiednie rejestry. Nie ponawiaj niepewnego zapisu ani nie wykonuj odwrotnej operacji bez nowej kwalifikacji. Lokalne backupy transakcji nie zastępują pełnej kopii HA. W razie potrzeby przywróć instalację według zweryfikowanej kopii i planu, uwzględniając stary plugin, zasób oraz konfigurację dashboardu; najpierw sprawdź stan i nie nadpisuj zmian nowszych niż kopia. Nie usuwaj dziennika ani danych przygotowania jako obejścia. Przywracanie niepewnego zakresu może być niedostępne.

**Granica dowodu:** syntetyczne testy natywnego Core potwierdziły nową konfigurację integracji, pojedynczy runtime/zasób, zachowanie danych i przejęcie rozpoznanego zasobu storage; stary zasób YAML bezpiecznie wstrzymuje konfigurację. Końcowa ścieżka HACS nowa/aktualizacja/pominięcie bridge, przycisk oczekującej aktualizacji, rollback i publiczna propagacja pozostają R11.B. Ta gałąź nie jest wydaniem.

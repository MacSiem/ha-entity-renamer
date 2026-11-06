# Entity Renamer — migration guide (candidate)

This guide describes an unreleased candidate and must be completed after the
real HACS migration tests. The existing 4.6.1/4.6.2 Dashboard installation
continues to use its existing card; downloading the compatibility JavaScript
alone does not install or configure the integration. Back up Home Assistant,
including dashboards and `.storage`, before changing installation type. Do not
repeat an operation with an uncertain result or delete recovery data.

The candidate integration contains the full card and backend. On a fresh
storage-mode installation it registers one versioned resource. For exactly one
recognized legacy HACS resource it updates that resource in place. Ambiguous
or unrecognized copies require manual review and are never silently removed.
For YAML resources the integration does not edit YAML; an existing legacy or
stale URL must be changed by the user to the candidate integration resource
before setup can finish. The exact HACS steps and rollback sequence are not yet
qualified and will be added after native migration QA.

# Entity Renamer — przewodnik migracji (kandydat)

Ten przewodnik opisuje niewydanego kandydata i zostanie uzupełniony po
rzeczywistych testach migracji HACS. Obecna instalacja Dashboard 4.6.1/4.6.2
korzysta z dotychczasowej karty; pobranie samego zgodnego JavaScriptu nie
instaluje ani nie konfiguruje integracji. Przed zmianą sposobu instalacji zrób
pełną kopię Home Assistant, w tym dashboardów i `.storage`. Nie powtarzaj
operacji o niepewnym wyniku i nie usuwaj danych odzyskiwania.

Kandydat integracji zawiera pełną kartę i backend. W świeżej instalacji storage
rejestruje jeden wersjonowany zasób. Dokładnie jeden rozpoznany stary zasób
HACS aktualizuje w miejscu. Niejednoznaczne lub nierozpoznane kopie wymagają
ręcznego sprawdzenia i nie są po cichu usuwane. Zasobów YAML integracja nie
edytuje; istniejący stary lub nieaktualny URL użytkownik musi zmienić na zasób
kandydata przed zakończeniem konfiguracji. Dokładne kroki HACS i rollback nie
zostały jeszcze zakwalifikowane i zostaną uzupełnione po natywnym QA migracji.

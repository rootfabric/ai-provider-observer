# Scaling R7

R7 масштабирует сложность через hierarchy, а не через гигантский Work Order.

```text
Parent Mission
  ├─ Work Order A → requirements/predicates/evidence
  ├─ Work Order B → requirements/predicates/evidence
  └─ Integration Work Order → cross-child requirements/evidence
```

Для каждого child сохраняется самостоятельный attempt ID, specification slice, Requirements Manifest и acceptance proof. Parent mission закрывается только после proven child outputs и parent-level integration predicates.

Для HIGH/CRITICAL добавьте protected remote audit sink: локальный Git/reflog не способен после уничтожения refs доказать, что abandoned commits никогда не удалялись. R7 local rewrite detection — fail-closed diagnostic при наличии reflog, не замена внешнему append-only audit.

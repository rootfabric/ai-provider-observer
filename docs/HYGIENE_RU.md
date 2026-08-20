# Harness Hygiene

## Цель

Сохранять Harness сильным без бесконечного наращивания правил и церемоний.

## Когда запускать hygiene review

- перед новым snapshot;
- после заметного роста обязательного контекста;
- при обнаружении prose/machine drift;
- после серии повторяющихся review findings;
- ориентировочно после N принятых checkpoints, указанного в policy.

## Классификация правил

### Protected

Safety, security, control и architecture invariants. Не становятся retirement candidate только потому, что редко срабатывают.

### Retirable

Process guards и empirical workarounds. Могут стать retirement candidate, если:

- уже механизированы;
- больше не применимы;
- заменены более сильным invariant;
- подтверждено benchmark/self-test прогоном, что удаление не ослабляет систему.

## Порядок улучшения

```text
повторяющийся дефект
    ↓
классификация
    ↓
можно механизировать?
    ├─ да → test/schema/linter/state machine
    │        ↓
    │      сократить prose
    └─ нет → короткое правило с trigger
```

## Что запрещено

- автоматически удалять protected rule;
- удалять правило только по возрасту;
- считать сокращение строк самоцелью;
- дублировать mutable machine state в root prose;
- добавлять новый источник project state ради удобства.

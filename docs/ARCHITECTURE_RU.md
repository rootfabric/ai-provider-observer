# Архитектура Hybrid Harness Snapshot R1

## 1. Два слоя истины

**Machine truth** хранит mutable state, policy, contracts и evidence. **Prose** объясняет модель и маршрутизирует чтение, но не повторяет текущие значения, которые меняются со временем.

Это специально устраняет класс дефектов «machine state уже H0.2, а инструкция всё ещё говорит H0.1».

## 2. Почему не глобальный WIP=1

Для большого проекта полезнее разделить:

- mutation concurrency — строго ограничена lease-политикой;
- read-only review/verification/analysis — может идти параллельно;
- конфликтующие authoritative mutations — сериализуются.

Так сохраняется безопасность без искусственного запрета полезного параллелизма.

## 3. Rule lifecycle

Rule registry хранит:

```text
id
class
source
applies_when
enforcement
enforced_by
retirement
prose_mode
```

Protected classes: `SAFETY_INVARIANT`, `SECURITY_INVARIANT`, `CONTROL_INVARIANT`, `ARCHITECTURE_INVARIANT`.

Они не удаляются автоматически даже если долго не нарушались.

## 4. Machine beats prose

Если правило можно надёжно проверять скриптом, тестом, schema или state machine, подробная инструкция должна быть сокращена до router-level ссылки. Это снижает instruction entropy и вероятность расхождения двух источников истины.

## 5. Self-test

Self-test намеренно создаёт плохие копии Harness и требует, чтобы каждая упала с ожидаемым кодом дефекта. Таким образом проверяется не только «зелёный baseline», но и способность ворот отличать плохое состояние от хорошего.

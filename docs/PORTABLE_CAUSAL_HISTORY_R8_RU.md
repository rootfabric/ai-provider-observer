# R8 Portable Causal History

R8 закрывает четыре класса обхода, обнаруженные на h-7.

## 1. Git replace refs

R7 мог получать разную truth для одного SHA в зависимости от локальных `refs/replace/*`. R8:

- принудительно выставляет `GIT_NO_REPLACE_OBJECTS=1` для machine proof;
- отдельным finding `GIT_REPLACE_REFS_PRESENT` запрещает сам факт активных replace refs;
- требует обычный clean clone как portability gate.

## 2. History immutability

R7 проверял `bytes(add) == bytes(HEAD)`. Это позволяло `A → B → A`.

R8 определяет immutable path так:

```text
add commit существует
AND path существует в HEAD
AND git log <add>..HEAD -- <path> пуст
```

То есть любое последующее касание пути уничтожает immutable proof.

## 3. Content-addressed consumer event

External attestation теперь потребляется не по одному path. Consumer event фиксирует:

```text
attestation path
SHA-256 exact bytes
Git blob OID exact bytes
```

Validator читает attestation из consumer commit и сравнивает обе identity. Final HEAD не может подменить bytes, которые видел consumer.

## 4. Closure trajectory

Net diff недостаточен. R8 перебирает каждый commit между:

```text
candidate → source_head
resulting_head → final HEAD
```

и проверяет каждый touched path против closure allowlist.

Поздний revert не стирает нарушение.

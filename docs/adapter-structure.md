# kanban-pro — adapter structure

How adapters are organized so that adding a backend (Hermes, Jira, Trello…) is a small,
consistent job. Grounded in the two existing adapters (`memory`, `native`).
**Status:** `BaseAdapter` (`adapters/_base.py`), the augmenting layer
(`core/augment.py` — WIP enforcement + comments/relations overlay slice), and the shared
contract suite (`tests/contract_suite.py`) are implemented; remote-adapter layout and
rate-limit descriptors apply from the first remote adapter on.

## Two kinds of adapter

| Kind | Examples | Traits |
|---|---|---|
| **Store adapter** | `memory`, `native` | Owns the data. Full capability set. No external backend. Implements *every* port method. Single module. |
| **Remote/proxy adapter** | `hermes`, `jira`, `trello` | Talks to an external backend over the network. **Thin** — declares only the capabilities the backend has natively; the rest are **polyfilled** by the augmenting layer. May be a package. |

The port (`KanbanBackend`) is identical for both — the difference is how much they implement
natively vs. lean on polyfill.

Bulk ops stay out of the port (SPEC: core loops over single-item methods). If an adapter
later gains a native batch endpoint, it exposes it via a separate optional protocol (e.g.
`BulkCapable`) that the core probes — bulk never joins `KanbanBackend` itself.

## The contract

Every adapter satisfies `kanban_pro.ports.KanbanBackend` **structurally** (no required
inheritance). Each adapter's test asserts conformance the way the current ones do:

```python
backend: KanbanBackend = MyAdapter(...)   # mypy verifies the shape
```

## Keeping remote adapters thin: `BaseAdapter`

A thin adapter shouldn't have to write 24 methods when its backend does 8. Plan:

- Add `adapters/_base.py` with a **`BaseAdapter`**: it implements *every* port method to
  raise `NotSupported` by default, and declares `capabilities = frozenset()`.
- A concrete remote adapter subclasses it and **overrides only the methods its backend does
  natively**, and declares those in `capabilities`.
- This satisfies the Protocol (all methods exist) while keeping the adapter small.

```python
class BaseAdapter:
    capabilities: frozenset[Capability] = frozenset()
    async def list_boards(self) -> list[Board]: raise NotSupported("list_boards")
    async def create_card(self, card: Card) -> Card: raise NotSupported("create_card")
    # ... default NotSupported for all port methods ...

class HermesAdapter(BaseAdapter):
    capabilities = frozenset({Capability.COMMENTS, Capability.LABELS, ...})
    async def list_boards(self) -> list[Board]: ...        # real
    async def create_card(self, card: Card) -> Card: ...   # real
    # everything it doesn't declare stays BaseAdapter's NotSupported — but the
    # augmenting layer polyfills those before they're ever reached (see below).
```

Store adapters (`memory`, `native`) don't need `BaseAdapter` — they override everything.

## The augmenting layer (lives in `core/`, wraps an adapter)

`AugmentingBackend = adapter + overlay` (SPEC decision 2). It is what the interfaces
actually call; it decides per operation:

```
for each op / capability:
  Fulfilment.NATIVE      -> call the adapter (it declared the capability)
  Fulfilment.POLYFILLED  -> fulfil via overlay (write-through into the backend's
                            container, else the native-store overlay)
  Fulfilment.UNAVAILABLE -> raise NotSupported
```

- Reads **merge**: canonical fields from the adapter + polyfilled bits (relations,
  checklists…) from the overlay/write-through.
- Because the augmenting layer gates on `capabilities` *before* dispatch, an adapter never
  needs a real method for a capability it didn't declare — `BaseAdapter`'s `NotSupported`
  is a backstop, not the normal path.
- The **overlay** is a `NativeStore` instance keyed to the backend's entity ids.

So a remote adapter's whole job is: **map what the backend natively does, declare it
honestly, and translate errors.** Polyfill is not its concern.

## Internal layout of a remote adapter

Small adapter → single module (`hermes.py`). Larger adapter → package:

```
adapters/jira/
  __init__.py       # JiraAdapter(BaseAdapter) — the KanbanBackend impl (orchestration)
  client.py         # httpx client: base URL, auth, pagination, + a RATE-LIMIT DESCRIPTOR
  mapping.py        # canonical <-> Jira DTO translation (both directions)
  capabilities.py   # the declared native Capability set
  errors.py         # Jira error/status -> canonical taxonomy (NotFound/Conflict/...)
```

Each remote adapter provides a **rate-limit descriptor** (where the 429 signal lives:
status code / header / body field — e.g. Linear returns HTTP 400) so the **core** retry
layer (SPEC decision 8) can back off uniformly without per-adapter retry code.

## Shared concerns (who owns what)

| Concern | Owner |
|---|---|
| canonical ⇄ backend mapping | the adapter (`mapping.py`) |
| error translation → taxonomy | the adapter (`errors.py`), using `ports` errors |
| capability declaration | the adapter (honest) |
| retry / backoff / rate-limit | **core**, driven by the adapter's rate-limit descriptor |
| idempotency / dedupe | **core** (decision 8) |
| polyfill / write-through / overlay | **core** augmenting layer (decision 2) |
| events / change-log / listeners | **core** (decision 9) |
| raw HTTP client / auth | the adapter (`client.py`) |

Adapters never leak raw backend types across the port; only canonical domain models cross.

## Registration & selection

A **profile** (SPEC decision 3) picks one adapter + its settings. Plan:

- **`config.py` holds the registry** — `name -> async factory() -> KanbanBackend`. (An
  earlier draft of this doc put it in `adapters/__init__.py`; it never lived there.)
- `config.py` resolves the active `--profile` to a registry entry, builds the adapter, and
  wraps it in the core stack: `RecordingBackend(AugmentingBackend(adapter), …)`.

The real thing, from `kanban_pro/config.py`:

```python
REGISTRY: dict[str, Callable[[], Awaitable[KanbanBackend]]] = {
    "default": _open_native,   # the native store IS the default profile
    "native": _open_native,
    "memory": _open_memory,    # ephemeral — tests / scratch boards
    "hermes": _open_hermes,    # HermesAdapter() — reads ~/.hermes SQLite, writes via CLI
}
```

Note the factories take **no settings argument** today, and `HermesAdapter` takes no
`base_url`/`token`: its reads are local SQLite and its writes shell out to `hermes kanban`.
Per-profile settings files arrive with the first genuinely remote adapter (SPEC decision 3).

## Testing: one shared contract suite

The `memory` and `native` tests currently duplicate the same scenarios. Plan: extract a
**parametrized contract suite** (`tests/contract.py`) that runs the same behavioral
assertions against every adapter fixture (memory, native, and a fake-remote using
`BaseAdapter` + a stub). New adapter = add a fixture, inherit the whole suite. This is how
we guarantee every adapter behaves identically at the port.

## Build order

1. `core/` augmenting layer + `BaseAdapter` (unblocks thin adapters).
2. Extract the shared contract test suite; point memory + native at it.
3. First remote adapter = `hermes` (after confirming its real API surface).
4. Then `jira` (package layout, the workflow/transition-rich case).

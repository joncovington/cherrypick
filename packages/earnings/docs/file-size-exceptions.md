# File Size Exceptions

Per CLAUDE.md: "Keep files under 500 lines"

This project has documented exceptions where file size exceeds the guideline due to strategic importance and refactoring risk:

## Core Infrastructure Files (High Refactoring Risk)

### `src/scanner.py` (1,038 lines)
**Justification:** Strategy-agnostic shared engine used by all strategy modules. Contains interdependent functions for earnings calendar, IV/RV calculations, winrate backtesting, liquidity gates, and candidate ranking. Refactoring to split functionality would require:
- Circular import resolution
- Database connection pooling across modules
- Shared state management between calendar, metrics, and ranking
- Extensive testing of cross-module interactions

**Current state:** Stable, fully tested, used by 9 strategy modules. Refactoring carries high risk of introducing bugs in live trading loop. **Acceptable exception.**

### `src/tt.py` (602 lines)
**Justification:** Broker-specific API wrapper (tastytrade). Contains tightly coupled methods for:
- Session management and authentication
- Quote fetching and option chain retrieval
- Order construction and submission
- Position management

Splitting would complicate credential handling and session state. **Acceptable exception.**

## Documentation Files (Content-Heavy)

### `docs/05-strategies.md` (713 lines)
**Justification:** Complete reference for all 9 strategies. Each strategy requires detailed entry conditions, examples, and exit logic. Could be split but currently organized for ease of reading alongside code review.

### `docs/08-trading-workflow.md` (560 lines)
**Justification:** Complete daily trading workflow with examples. Split would fragment the user mental model. Acceptable exception.

### Other docs (508-521 lines each)
**Status:** Near limit but acceptable. Consider splitting in next refactor cycle.

## Going Forward

**Policy:**
- ✅ New Python files MUST stay under 500 lines
- ✅ New documentation sections SHOULD stay under 500 lines
- ⚠️ Existing exceptions documented above
- 🔄 When modifying exception files, keep size increase minimal
- 📋 If any file exceeds 800 lines, refactoring becomes mandatory

## Review Triggers

If any file grows beyond its current size by 20%+:
- Evaluate if split is now feasible
- Document rationale for continued exception
- Consider impact on maintainability

# Contributing to ATP Python SDK

Thank you for your interest in contributing to the Agent Trust Protocol Python SDK!

## 🚀 Quick Start

```bash
# Clone the repository
git clone https://github.com/aiquilibria/atp-python.git
cd atp-python

# Install in development mode
uv pip install -e ".[dev]"

# Run tests
./scripts/test.sh
```

## 📋 Development Workflow

### 1. Code Style

We use **ruff** for formatting and linting:

```bash
# Format code
./scripts/format.sh

# Check linting
./scripts/lint.sh
```

### 2. Testing

All contributions must include tests:

```bash
# Run all tests
./scripts/test.sh

# Run specific test
uv run pytest tests/test_client.py -v

# Run with coverage
uv run pytest --cov=src/atp --cov-report=html
```

### 3. Type Checking

We use **mypy** for type checking:

```bash
# Type check (included in test script)
uv run mypy src/atp
```

## 🔧 Code Standards

- **Python Version**: 3.12+
- **Line Length**: 100 characters
- **Docstrings**: Required for public APIs
- **Type Hints**: Strongly encouraged
- **Tests**: Required for new features

## 🎯 What to Contribute

### High Priority
- 🐛 Bug fixes
- 📝 Documentation improvements
- 🧪 Test coverage improvements
- 🔌 Framework adapters (LangChain, AutoGPT, etc.)

### Welcome Contributions
- ✨ New features (discuss first via issue)
- 🚀 Performance improvements
- 📖 Examples and tutorials
- 🌐 Internationalization

## 📝 Pull Request Process

1. **Fork** the repository
2. **Create** a feature branch (`git checkout -b feature/amazing-feature`)
3. **Make** your changes
4. **Run** all checks: `./scripts/check.sh`
5. **Commit** with clear message
6. **Push** to your fork
7. **Open** a Pull Request

### PR Requirements

- ✅ All tests passing
- ✅ Code formatted with ruff
- ✅ No linting errors
- ✅ Type checks pass (if applicable)
- ✅ Clear description of changes
- ✅ Related issue linked (if applicable)

## 🐛 Reporting Bugs

**Before submitting:**
- Search existing issues
- Try latest version

**Include in bug report:**
- Python version
- ATP SDK version
- Minimal reproduction code
- Expected vs actual behavior
- Error messages/stack traces

## 💡 Feature Requests

**Before requesting:**
- Check if already requested
- Consider if it fits ATP's scope

**Include in request:**
- Clear use case
- Proposed API (if applicable)
- Why it benefits ATP users

## 🔌 Creating Framework Adapters

Want to integrate ATP with a new framework? Great!

**Structure:**
```python
# src/atp/adapters/your_framework/
├── __init__.py
├── adapter.py      # Main adapter class
└── README.md       # Framework-specific docs
```

**Requirements:**
- Inherit from `FrameworkAdapter` base class
- Include tests
- Add example in `examples/your_framework/`
- Document in `docs/ADAPTERS.md`

See `src/atp/adapters/a2a/` as reference implementation.

## 📖 Documentation

Documentation improvements are always welcome:

- **README.md** - Main project documentation
- **docs/** - Detailed guides
- **Docstrings** - API documentation
- **Examples** - Usage tutorials

## 🤝 Code of Conduct

- Be respectful and inclusive
- Focus on constructive feedback
- Help maintain a welcoming community

## ❓ Questions?

- 💬 **Discord**: [Join our community](#)
- 📧 **Email**: support@aiquilibria.com
- 🐛 **Issues**: [GitHub Issues](#)

## 📄 License

By contributing, you agree that your contributions will be licensed under the same license as the project (TBD - Phase 3.1).

---

**Thank you for contributing to ATP!** 🎉

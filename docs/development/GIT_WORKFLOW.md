# Professional Git Workflow Guide

**Reference Guide for Managing Code Changes with Git & GitHub**

---

## Table of Contents

- [Core Principle](#core-principle)
- [Standard Workflow](#standard-workflow)
- [Branch Naming Convention](#branch-naming-convention)
- [Commit Message Best Practices](#commit-message-best-practices)
- [Common Operations](#common-operations)
- [Working with Versions](#working-with-versions)
- [Troubleshooting](#troubleshooting)
- [Quick Reference](#quick-reference)

---

## Core Principle

**Never work directly on `main` branch. Always use feature branches.**

### Why?

- ✅ Main branch stays stable and production-ready
- ✅ Easy to rollback if issues occur
- ✅ Multiple features can be developed in parallel
- ✅ Clean history and code review process
- ✅ Safe experimentation without breaking production

---

## Standard Workflow

### Complete Step-by-Step Process

```bash
# ============================================
# STEP 1: Start from clean main branch
# ============================================
cd /home/linuxuser/nautilus_AlgVex
git checkout main
git pull origin main  # Get latest changes from GitHub

# ============================================
# STEP 2: Create feature branch
# ============================================
git checkout -b feature/descriptive-name
# Examples:
# - feature/add-stop-loss
# - fix/rsi-calculation
# - improvement/optimize-indicators

# ============================================
# STEP 3: Make your changes
# ============================================
# Edit files as needed
nano main_live.py
nano strategy/ai_strategy.py

# Test your changes!
python3 main_live.py

# ============================================
# STEP 4: Stage and commit with detailed message
# ============================================
git add .
# Or stage specific files:
# git add main_live.py strategy/ai_strategy.py

git commit -m "type: Short summary (50 chars max)

Detailed description:
- What changed
- Why it changed
- How it affects the system
- Any breaking changes or migration notes

Testing done:
- Describe testing approach
- Results or metrics

Fixes #123
Related to #456"

# ============================================
# STEP 5: Push branch to GitHub
# ============================================
git push origin feature/descriptive-name

# ============================================
# STEP 6: Create Pull Request (on GitHub)
# ============================================
# 1. Go to: https://github.com/FelixWayne0318/AlgVex
# 2. Click "Compare & pull request" button
# 3. Fill in Pull Request description
# 4. Request review (if working with team)
# 5. Merge when approved and tests pass

# ============================================
# STEP 7: After merge, clean up
# ============================================
git checkout main
git pull origin main  # Get merged changes

# Delete local branch
git branch -d feature/descriptive-name

# Delete remote branch (optional)
git push origin --delete feature/descriptive-name

# ============================================
# STEP 8: Start next feature (repeat from Step 2)
# ============================================
```

---

## Branch Naming Convention

### Format: `<type>/<short-description>`

### Branch Types

```bash
# New Features
feature/add-trailing-stop-loss
feature/multi-timeframe-analysis
feature/telegram-notifications
feature/sentiment-analysis

# Bug Fixes
fix/indicator-initialization
fix/connection-timeout
fix/rsi-calculation-error
hotfix/critical-bug  # For urgent production fixes

# Code Improvements
refactor/cleanup-strategy-code
refactor/modularize-indicators
improvement/optimize-api-calls
perf/cache-calculations

# Documentation
docs/update-readme
docs/add-api-reference
docs/improve-quickstart-guide

# Experiments (usually not merged)
experiment/new-ai-model
experiment/alternative-indicators
test/binance-testnet

# Release Branches
release/v1.2.0
release/v2.0.0-beta
```

### Naming Guidelines

- Use lowercase
- Use hyphens, not underscores
- Be descriptive but concise
- Include issue number if applicable: `fix/issue-42-rsi-bug`

---

## Commit Message Best Practices

### Structure

```
<type>: <short summary> (50 characters max)

<detailed description - explain what, why, and how>
- Bullet point 1
- Bullet point 2
- Bullet point 3

<optional footer with references>
```

### Commit Types

```
feat:     New feature
fix:      Bug fix
docs:     Documentation only changes
style:    Formatting, missing semicolons, etc (no code change)
refactor: Code restructuring (no behavior change)
perf:     Performance improvement
test:     Adding or updating tests
chore:    Maintenance tasks (dependencies, build, etc)
```

### Good Examples

```bash
# Example 1: New Feature
git commit -m "feat: Add dynamic stop-loss based on ATR

- Implemented ATR (Average True Range) calculation
- Added stop_loss_atr_multiplier config parameter (default: 2.0)
- Integrated ATR-based stop placement in position manager
- Stop-loss now adjusts based on market volatility
- Tested with historical data showing 15% better risk/reward

Configuration:
- atr_period: 14 (configurable in configs/base.yaml)
- stop_loss_atr_multiplier: 2.0 (distance from entry)

Closes #42"

# Example 2: Bug Fix
git commit -m "fix: Prevent division by zero in RSI calculation

- Added boundary checks for RSI extreme values (0 and 100)
- Handle edge case when price has no variation
- Added unit tests for RSI edge cases
- Prevents strategy crashes during market extremes

Impact:
- Strategy now stable during low-volatility periods
- No more unexpected crashes

Fixes #67"

# Example 3: Refactoring
git commit -m "refactor: Extract indicator logic into separate module

- Moved all indicator calculations to technical_manager.py
- Reduced main strategy file size by 40%
- Improved code organization and maintainability
- No behavioral changes - all tests still pass

Benefits:
- Easier to add new indicators
- Better separation of concerns
- More testable code"

# Example 4: Performance
git commit -m "perf: Cache SMA calculations to reduce redundancy

- Implemented caching for SMA values
- Reduced CPU usage by ~30% during analysis
- Memory usage increased by ~5MB (acceptable tradeoff)
- Maintains backward compatibility

Benchmark Results:
- Before: 250ms per analysis cycle
- After: 175ms per analysis cycle
- Improvement: 30% faster"

# Example 5: Documentation
git commit -m "docs: Add comprehensive deployment guide

- Created step-by-step deployment instructions
- Added troubleshooting section with common issues
- Included AWS and VPS setup examples
- Updated API key configuration section
- Added security best practices

New sections:
- Installation on Ubuntu
- Systemd service setup
- Monitoring and logging
- Emergency procedures"
```

### Bad Examples (Avoid These)

```bash
# ❌ Too vague
git commit -m "fix bug"
git commit -m "update code"
git commit -m "changes"
git commit -m "wip"

# ❌ No details
git commit -m "Modified main_live.py"
git commit -m "Updated strategy"

# ❌ Multiple unrelated changes
git commit -m "Fix bug, add feature, update docs, refactor code"
```

---

## Common Operations

### Starting Work on a New Feature

```bash
# Update main
git checkout main
git pull origin main

# Create feature branch
git checkout -b feature/my-new-feature

# Make changes, test, commit
# ... edit files ...
git add .
git commit -m "feat: detailed message"

# Push to GitHub
git push origin feature/my-new-feature
```

### Fixing a Bug

```bash
# From main, create fix branch
git checkout main
git pull origin main
git checkout -b fix/bug-description

# Fix the bug
# ... edit files ...
git add .
git commit -m "fix: detailed explanation"

# Push
git push origin fix/bug-description
```

### Updating Your Branch with Latest Main

```bash
# Your feature branch may be outdated if main changed
git checkout main
git pull origin main

git checkout feature/my-feature
git merge main
# Resolve conflicts if any

git push origin feature/my-feature
```

### Checking What Changed

```bash
# View uncommitted changes
git status
git diff

# View changes in staged files
git diff --staged

# View commit history
git log --oneline -10
git log --graph --oneline --all

# View specific file history
git log --oneline -- main_live.py

# View what changed in a commit
git show <commit-hash>
```

### Undoing Changes

```bash
# Discard uncommitted changes in a file
git checkout -- main_live.py

# Unstage a file (keep changes)
git reset HEAD main_live.py

# Undo last commit (keep changes)
git reset --soft HEAD~1

# Undo last commit (discard changes) ⚠️
git reset --hard HEAD~1

# Revert a commit (creates new commit)
git revert <commit-hash>
```

---

## Working with Versions

### Viewing Old Versions

```bash
# View commit history
git log --oneline

# Checkout old version (read-only)
git checkout <commit-hash>
# Look around, test...

# Return to current version
git checkout main
```

### Restoring Specific Files from Old Version

```bash
# Find the commit you want
git log --oneline

# Restore specific file from that commit
git checkout <commit-hash> -- path/to/file.py

# Commit the restoration
git add path/to/file.py
git commit -m "Restore file.py to version <commit-hash>"
git push origin main
```

### Creating Version Branches

```bash
# Create branch from old commit
git checkout -b v1.0-maintenance <commit-hash>

# Now you can work on old version
# ... make fixes ...
git commit -m "Fix bug in v1.0"
git push origin v1.0-maintenance

# You now have two maintained versions:
# - main: current development
# - v1.0-maintenance: old stable version
```

### Tagging Releases

```bash
# Create annotated tag for release
git tag -a v1.0.0 -m "Release version 1.0.0

Features:
- Feature 1
- Feature 2

Bug fixes:
- Fix 1
- Fix 2"

# Push tag to GitHub
git push origin v1.0.0

# List all tags
git tag -l

# Checkout specific tag
git checkout v1.0.0
```

---

## Troubleshooting

### "I committed to main by mistake!"

```bash
# If not pushed yet:
git reset --soft HEAD~1  # Undo commit, keep changes
git checkout -b feature/my-feature  # Create branch
git commit -m "proper message"
git push origin feature/my-feature

# If already pushed (careful!):
git revert HEAD  # Create reverse commit
git push origin main
```

### "I have merge conflicts!"

```bash
# During merge, Git will show conflicts
git status  # Shows conflicted files

# Edit files to resolve conflicts
nano conflicted_file.py
# Look for <<<<<<< ======= >>>>>>> markers
# Choose which version to keep

# After resolving
git add conflicted_file.py
git commit -m "Merge main into feature/my-feature"
git push origin feature/my-feature
```

### "I want to undo my last push!"

```bash
# If you're the only one using the branch:
git reset --hard HEAD~1
git push -f origin branch-name

# If others might have pulled:
git revert HEAD
git push origin branch-name  # Creates reverse commit
```

### "I'm in 'detached HEAD' state!"

```bash
# This happens when you checkout a commit directly
# To save your work, create a branch:
git checkout -b temp-branch

# Or just return to a branch:
git checkout main
```

### "I need to save changes but switch branches"

```bash
# Stash your changes temporarily
git stash

# Switch branches
git checkout other-branch

# Come back and restore
git checkout original-branch
git stash pop
```

---

## Quick Reference

### Daily Commands

```bash
# Check status
git status

# View changes
git diff

# Stage changes
git add .
git add specific_file.py

# Commit
git commit -m "type: message"

# Push
git push origin branch-name

# Pull latest
git pull origin main

# Switch branch
git checkout branch-name

# Create and switch
git checkout -b new-branch

# View history
git log --oneline -10
```

### Branch Management

```bash
# List branches
git branch                    # Local branches
git branch -a                 # All branches (local + remote)

# Create branch
git branch feature/new-feature

# Switch branch
git checkout feature/new-feature

# Create and switch (shortcut)
git checkout -b feature/new-feature

# Delete local branch
git branch -d feature/old-feature

# Delete remote branch
git push origin --delete feature/old-feature

# Rename current branch
git branch -m new-name
```

### Remote Operations

```bash
# View remotes
git remote -v

# Add remote
git remote add origin https://github.com/user/repo.git

# Fetch updates (doesn't merge)
git fetch origin

# Pull (fetch + merge)
git pull origin main

# Push branch
git push origin branch-name

# Push and set upstream
git push -u origin branch-name

# Force push (careful!)
git push -f origin branch-name
```

### Information Commands

```bash
# Current branch and status
git status

# Commit history
git log
git log --oneline
git log --graph --oneline --all

# Show commit details
git show <commit-hash>

# File history
git log -- path/to/file.py

# Who changed what
git blame file.py

# Difference between branches
git diff main..feature-branch

# List changed files
git diff --name-only
```

---

## Workflow Checklist

### Before Starting Work

```
✅ git checkout main
✅ git pull origin main
✅ git checkout -b feature/descriptive-name
```

### During Development

```
✅ Make small, focused changes
✅ Test your changes locally
✅ Write clear commit messages
✅ Commit frequently (logical chunks)
```

### Before Pushing

```
✅ Review your changes: git diff
✅ Check what will be committed: git status
✅ Ensure tests pass
✅ Update documentation if needed
```

### After Completing Feature

```
✅ git push origin feature-branch
✅ Create Pull Request on GitHub
✅ Request review (if team)
✅ Merge to main when approved
✅ Delete feature branch
✅ git checkout main && git pull
```

---

## Advanced Tips

### Viewing Graphical History

```bash
# Visual branch history
git log --graph --oneline --all --decorate

# Or create an alias:
git config --global alias.tree "log --graph --oneline --all --decorate"
# Then use: git tree
```

### Cherry-Pick Specific Commits

```bash
# Apply specific commit to current branch
git cherry-pick <commit-hash>

# Useful for pulling bug fixes into release branches
```

### Interactive Rebase (Clean History)

```bash
# Rebase last 3 commits
git rebase -i HEAD~3

# Options:
# - pick: keep commit
# - reword: change message
# - squash: combine with previous
# - drop: remove commit
```

### Useful Aliases

Add to your `~/.gitconfig`:

```bash
[alias]
    st = status
    co = checkout
    br = branch
    cm = commit
    lg = log --graph --oneline --all --decorate
    last = log -1 HEAD
    unstage = reset HEAD --
    undo = reset --soft HEAD~1
```

---

## Best Practices Summary

### DO ✅

- Create feature branches for all changes
- Write detailed commit messages
- Commit frequently (small, logical chunks)
- Test before committing
- Pull latest main before creating branches
- Keep commits focused (one purpose per commit)
- Use descriptive branch names

### DON'T ❌

- Commit directly to main
- Write vague commit messages ("fix", "update")
- Commit large, unrelated changes together
- Force push to shared branches
- Leave merge conflicts unresolved
- Commit sensitive data (.env files, API keys)
- Use confusing branch names

---

## Resources

### Learning Resources

- **Interactive Tutorial**: https://learngitbranching.js.org/
- **Pro Git Book**: https://git-scm.com/book/en/v2
- **Atlassian Tutorials**: https://www.atlassian.com/git/tutorials
- **Git Cheat Sheet**: https://education.github.com/git-cheat-sheet-education.pdf

### Tools

- **GitHub Desktop**: Visual Git client
- **GitKraken**: Advanced visual client
- **VS Code**: Built-in Git integration

### Help

- **Git Documentation**: https://git-scm.com/doc
- **Stack Overflow**: https://stackoverflow.com/questions/tagged/git
- **GitHub Community**: https://github.community/

---

## Project-Specific Workflow

### For This Trading Bot

```bash
# Common feature branches:
feature/add-indicator          # New technical indicator
feature/improve-ai-prompt      # AI model improvements
feature/add-notification       # Alerts/notifications
fix/calculation-error          # Bug fixes
docs/update-readme            # Documentation
refactor/optimize-performance  # Code improvements

# Example workflow:
cd /home/linuxuser/nautilus_AlgVex
git checkout main
git pull origin main
git checkout -b feature/add-macd-divergence

# Make changes...
nano indicators/technical_manager.py

# Test
python run_quick_test.py

# Commit
git add indicators/technical_manager.py
git commit -m "feat: Add MACD divergence detection

- Implemented bullish and bearish divergence detection
- Added divergence signals to technical analysis output
- Integrated divergence into AI decision-making prompt
- Tested with historical data showing improved entry timing

Configuration:
- Uses existing MACD calculation
- Looks back 20 bars for divergence patterns
- Adds 'macd_divergence' field to technical_data"

# Push
git push origin feature/add-macd-divergence

# Create PR on GitHub, merge, clean up
git checkout main
git pull origin main
git branch -d feature/add-macd-divergence
```

---

## Emergency Procedures

### If Strategy is Broken After Update

```bash
# Option 1: Revert last merge
git log --oneline -5  # Find merge commit
git revert <merge-commit-hash>
git push origin main

# Option 2: Reset to working version
git log --oneline  # Find working commit
git reset --hard <working-commit>
git push -f origin main  # ⚠️ Use carefully!

# Option 3: Create hotfix from old version
git checkout <working-commit>
git checkout -b hotfix/emergency-fix
# Deploy this version while you fix main
```

### If You Need to Delete Sensitive Data

```bash
# If you accidentally committed API keys:
# 1. Remove from repository
git rm .env
git commit -m "Remove sensitive file"

# 2. Remove from history (⚠️ advanced)
git filter-branch --force --index-filter \
  "git rm --cached --ignore-unmatch .env" \
  --prune-empty --tag-name-filter cat -- --all

# 3. Force push
git push -f origin main

# 4. Rotate compromised API keys immediately!
```

---

**Last Updated**: 2026-02
**For**: AlgVex - NautilusTrader DeepSeek Trading Bot
**Repository**: https://github.com/FelixWayne0318/AlgVex

---

*Remember: The main branch should always be deployable. Use feature branches for all development work!* 🚀


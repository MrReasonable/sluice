# job-sluice.plugin.zsh -- zsh shell-completion activation for job-sluice.
#
# Loadable by oh-my-zsh (copy or symlink this directory to $ZSH_CUSTOM/plugins/job-sluice,
# then add `job-sluice` to your `plugins=(...)` array) or zinit (see docs/INSTALL.md for
# the exact snippet). Both loaders just source every `*.plugin.zsh` file in a plugin
# directory, so this file is the whole plugin.
#
# WHY THIS IS AN ACTIVATION LINE, NOT A STATIC COMPLETION FILE. `job-sluice`'s completion
# is provided by argcomplete (an optional `job-sluice[completion]` extra -- see README.md's
# Install section), which introspects the REAL, live argparse tree on every TAB press
# rather than a pre-generated file that could drift from the CLI it describes. That is
# also how completion reaches real values: `--source`/`ingest enable ID` complete against
# the actual registered source ids, and `track confirm --to` completes against the actual
# status vocabulary, both read live rather than hand-listed here.
#
# Both guards below are deliberate no-ops, not errors: `job-sluice` not being on $PATH
# means it isn't installed yet, and `register-python-argcomplete` not being on $PATH means
# the `completion` extra specifically hasn't been installed (`pip install
# 'job-sluice[completion]'`) even though the base package has been. Either way, a shell plugin
# staying silent about a prerequisite that is simply not met yet is the expected shape --
# failing loudly belongs to job-sluice's OWN commands (see CLAUDE.md's "fail loudly at
# construction" rule), not to a plugin loader deciding whether to activate.
if (( $+commands[job-sluice] )) && (( $+commands[register-python-argcomplete] )); then
  eval "$(register-python-argcomplete job-sluice)"
fi

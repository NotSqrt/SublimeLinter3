# linter.py
# Part of SublimeLinter, a code checking framework for Sublime Text 3
#
# Project: https://github.com/SublimeLinter/sublimelinter
# License: MIT

import re
import sublime

from .highlight import Highlight
from . import persist
from . import util

SYNTAX_RE = re.compile(r'/([^/]+)\.tmLanguage$')


class Registrar(type):
    '''This metaclass registers the linter when the class is declared.'''
    def __init__(cls, name, bases, attrs):
        if bases:
            persist.register_linter(cls, name, attrs)


class Linter(metaclass=Registrar):
    '''
    The base class for linters. Subclasses must at a minimum define
    the attributes language, cmd, and regex.
    '''

    #
    # Error types
    #
    WARNING = 0
    ERROR = 1

    #
    # Public attributes
    #

    # The name of the linter's language for display purposes.
    # By convention this is all lowercase.
    language = ''

    # A string, tuple or callable that returns a string or tuple, containing the
    # command line arguments used to lint.
    cmd = ''

    # A regex pattern used to extract information from the linter's executable output.
    regex = ''

    # Set to True if the linter outputs multiple errors or multiline errors. When True,
    # regex will be created with the re.MULTILINE flag.
    multiline = False

    # If you want to set flags on the regex other than re.MULTILINE, set this.
    re_flags = 0

    # If the linter executable cannot receive from stdin and requires a temp file,
    # set this attribute to the suffix of the temp file.
    tempfile_suffix = None

    # Tab width
    tab_width = 1

    # If you want to limit the linter to specific portions of the source
    # based on a scope selector, set this attribute to the selector. For example,
    # in an html file with embedded php, you would set the selector for a php
    # linter to 'source.php'.
    selector = None

    # Set to True if you want errors to be outlined for this linter.
    outline = True

    # If you want to provide default settings for the linter, set this attribute.
    defaults = None

    #
    # Internal class storage, do not set
    #
    errors = None
    highlight = None
    lint_settings = None

    def __init__(self, view, syntax, filename=None):
        self.view = view
        self.syntax = syntax
        self.filename = filename

        if self.regex:
            if self.multiline:
                self.re_flags |= re.MULTILINE

            try:
                self.regex = re.compile(self.regex, self.re_flags)
            except:
                persist.debug('error compiling regex for {}'.format(self.language))

        self.highlight = Highlight()

    @classmethod
    def get_settings(cls):
        '''Return the default settings for this linter, merged with the user settings.'''
        linters = persist.settings.get('linters', {})
        settings = cls.defaults or {}
        settings.update(linters.get(cls.__name__, {}))
        return settings

    @property
    def settings(self):
        return self.get_settings()

    @classmethod
    def assign(cls, view, reassign=False):
        '''
        Assign a view to an instance of a linter.
        Find a linter for a specified view if possible, then add it to our view <--> lint class map and return it.
        Each view has its own linter so that linters can store persistent data about a view.
        '''
        vid = view.id()
        persist.views[vid] = view

        settings = view.settings()
        syntax = settings.get('syntax')

        if not syntax:
            cls.remove(vid)
            return

        match = SYNTAX_RE.search(syntax)

        if match:
            syntax = match.group(1)
        else:
            syntax = ''

        if syntax:
            if vid in persist.linters and persist.linters[vid] and not reassign:
                # If a linter in the set of linters for the given view
                # already handles the view's syntax, we have nothing more to do.
                for linter in tuple(persist.linters[vid]):
                    if linter.syntax == syntax:
                        return

            linters = set()

            for name, linter_class in persist.languages.items():
                if linter_class.can_lint(syntax):
                    linter = linter_class(view, syntax, view.file_name())
                    linters.add(linter)

            persist.linters[vid] = linters
            return linters

        cls.remove(vid)

    @classmethod
    def remove(cls, vid):
        '''Remove a the mapping between a view and its set of linters.'''
        if vid in persist.linters:
            for linters in persist.linters[vid]:
                linters.clear()

            del persist.linters[vid]

    @classmethod
    def reload(cls, mod=None):
        '''Reload all linters, optionally filtering by module.'''

        # Merge linter default settings with user settings
        linter_settings = persist.settings.get('linters', {})

        for name, linter in persist.languages.items():
            settings = linter_settings.get(name, {})
            defaults = (linter.defaults or {}).copy()
            defaults.update(settings)
            linter.lint_settings = defaults

        for vid, linters in persist.linters.items():
            for linter in linters:
                if mod and linter.__module__ != mod:
                    continue

                linter.clear()
                persist.linters[vid].remove(linter)
                linter = persist.languages[linter.name](linter.view, linter.syntax, linter.filename)
                persist.linters[vid].add(linter)
                linter.draw()

        return

    @classmethod
    def text(cls, view):
        '''Returns the entire text of a view.'''
        return view.substr(sublime.Region(0, view.size()))

    @classmethod
    def get_view(cls, vid):
        '''Returns the view object with the given id.'''
        return persist.views.get(vid)

    @classmethod
    def get_linters(cls, vid):
        '''Returns a tuple of linters for the view with the given id.'''
        if vid in persist.linters:
            return tuple(persist.linters[vid])

        return ()

    @classmethod
    def get_selectors(cls, vid):
        '''Returns a list of scope selectors for all linters for the view with the given id.'''
        return [
            (linter.selector, linter)
            for linter in cls.get_linters(vid)
            if linter.selector
        ]

    @classmethod
    def lint_view(cls, vid, filename, code, sections, callback):
        if not code or vid not in persist.linters:
            return

        linters = list(persist.linters.get(vid))

        if not linters:
            return

        filename = filename or 'untitled'
        linter_list = (', '.join(l.name for l in linters))
        persist.debug('lint \'{}\' as {}'.format(filename, linter_list))

        for linter in linters:
            if linter.settings.get('disable'):
                continue

            if not linter.selector:
                linter.reset(code, filename=filename)
                linter.lint()

        selectors = Linter.get_selectors(vid)

        for sel, linter in selectors:
            linters.append(linter)

            if sel in sections:
                linter.reset(code, filename=filename)
                errors = {}

                for line_offset, left, right in sections[sel]:
                    linter.highlight.move_to(line_offset, left)
                    linter.code = code[left:right]
                    linter.errors = {}
                    linter.lint()

                    for line, error in linter.errors.items():
                        errors[line + line_offset] = error

                linter.errors = errors

        # Merge our result back to the main thread
        callback(cls.get_view(vid), linters)

    def reset(self, code, filename=None, highlight=None):
        self.errors = {}
        self.code = code
        self.filename = filename or self.filename
        self.highlight = highlight or Highlight(
            self.code, outline=self.outline)

    def lint(self):
        if not (self.language and self.cmd and self.regex):
            raise NotImplementedError

        if callable(self.cmd):
            cmd = self.cmd()
        else:
            cmd = self.cmd

        if not cmd:
            return

        if not isinstance(cmd, (tuple, list)):
            cmd = (cmd,)

        output = self.run(cmd, self.code)

        if not output:
            return

        persist.debug('{} output:\n'.format(self.__class__.__name__) + output)

        for match, row, col, error_type, message, near in self.find_errors(output):
            if match and row is not None:
                if col is not None:
                    # Adjust column numbers to match the linter's tabs if necessary
                    if self.tab_width > 1:
                        start, end = self.highlight.full_line(row)
                        code_line = self.code[start:end]
                        diff = 0

                        for i in range(len(code_line)):
                            if code_line[i] == '\t':
                                diff += (self.tab_width - 1)

                            if col - diff <= i:
                                col = i
                                break

                    self.highlight.range(row, col)
                elif near:
                    self.highlight.near(row, near)
                else:
                    self.highlight.line(row)

                self.error(row, col, message)

    def draw(self, prefix='lint'):
        self.highlight.draw(self.view, prefix)

    def clear(self, prefix='lint'):
        self.highlight.clear(self.view, prefix)

    # helper methods

    @classmethod
    def can_lint(cls, language):
        return Linter.linter_can_lint(cls, language)

    @staticmethod
    def linter_can_lint(cls, language):
        language = language.lower()

        if cls.language:
            if language == cls.language:
                return True
            elif isinstance(cls.language, (tuple, list)) and language in cls.language:
                return True

        return False

    def error(self, line, col, error):
        self.highlight.line(line)
        error = ((col or 0), str(error))

        if line in self.errors:
            self.errors[line].append(error)
        else:
            self.errors[line] = [error]

    def find_errors(self, output):
        if self.multiline:
            errors = self.regex.finditer(output)

            if errors:
                for error in errors:
                    yield self.split_match(error)
            else:
                yield self.split_match(None)
        else:
            for line in output.splitlines():
                yield self.match_error(self.regex, line.strip())

    def split_match(self, match):
        if match:
            items = {'line': None, 'col': None, 'type': None, 'error': '', 'near': None}
            items.update(match.groupdict())
            row, col, error_type, error, near = [items[k] for k in ('line', 'col', 'type', 'error', 'near')]

            row = int(row) - 1

            if col:
                col = int(col) - 1

            return match, row, col, error_type, error, near

        return match, None, None, None, '', None

    def match_error(self, r, line):
        return self.split_match(r.match(line))

    # Subclasses may need to override this in complex cases
    def run(self, cmd, code):
        if self.tempfile_suffix:
            return self.tmpfile(cmd, suffix=self.tempfile_suffix)
        else:
            return self.communicate(cmd, code)

    # popen wrappers
    def communicate(self, cmd, code):
        return util.communicate(cmd, code)

    def tmpfile(self, cmd, code, suffix=''):
        return util.tmpfile(cmd, code, suffix or self.tempfile_suffix)

    def tmpdir(self, cmd, files, code):
        return util.tmpdir(cmd, files, self.filename, code)

    def popen(self, cmd, env=None):
        return util.popen(cmd, env)

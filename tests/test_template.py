"""Tests for the Go-style template engine."""

from pyackett.engine.template import apply_template


class TestVariableSubstitution:
    def test_simple_variable(self):
        result = apply_template("Hello {{ .Name }}", {".Name": "World"})
        assert result == "Hello World"

    def test_nested_variable(self):
        result = apply_template(
            "{{ .Config.username }}",
            {".Config.username": "admin"},
        )
        assert result == "admin"

    def test_missing_variable(self):
        result = apply_template("Hello {{ .Missing }}", {})
        assert result == "Hello "

    def test_no_template(self):
        assert apply_template("plain text", {}) == "plain text"

    def test_empty(self):
        assert apply_template("", {}) == ""

    def test_none(self):
        assert apply_template(None, {}) == ""


class TestConditionals:
    def test_if_else_true(self):
        result = apply_template(
            "{{ if .HasValue }}yes{{ else }}no{{ end }}",
            {".HasValue": "something"},
        )
        assert result == "yes"

    def test_if_else_false(self):
        result = apply_template(
            "{{ if .HasValue }}yes{{ else }}no{{ end }}",
            {".HasValue": ""},
        )
        assert result == "no"

    def test_if_else_none(self):
        result = apply_template(
            "{{ if .HasValue }}yes{{ else }}no{{ end }}",
            {".HasValue": None},
        )
        assert result == "no"

    def test_if_no_else(self):
        result = apply_template(
            "before{{ if .Show }}CONTENT{{ end }}after",
            {".Show": "yes"},
        )
        assert result == "beforeCONTENTafter"

    def test_if_no_else_false(self):
        result = apply_template(
            "before{{ if .Show }}CONTENT{{ end }}after",
            {".Show": ""},
        )
        assert result == "beforeafter"

    def test_nested_if(self):
        result = apply_template(
            "{{ if .A }}{{ if .B }}both{{ else }}only-a{{ end }}{{ else }}none{{ end }}",
            {".A": "yes", ".B": "yes"},
        )
        assert result == "both"

    def test_true_false_literals(self):
        result = apply_template(
            "{{ if .True }}yes{{ else }}no{{ end }}",
            {".True": ".True"},
        )
        assert result == "yes"


class TestRange:
    def test_simple_range(self):
        result = apply_template(
            "{{ range .Items }}[{{ . }}]{{ end }}",
            {".Items": ["a", "b", "c"]},
        )
        assert result == "[a][b][c]"

    def test_empty_range(self):
        result = apply_template(
            "{{ range .Items }}[{{ . }}]{{ end }}",
            {".Items": []},
        )
        assert result == ""

    def test_range_with_separator(self):
        result = apply_template(
            "{{ range .Categories }}{{ . }},{{ end }}",
            {".Categories": ["1", "2", "3"]},
        )
        assert result == "1,2,3,"


class TestLogicFunctions:
    def test_and_both_truthy(self):
        result = apply_template(
            "{{ if and .A .B }}yes{{ else }}no{{ end }}",
            {".A": "x", ".B": "y"},
        )
        assert result == "yes"

    def test_and_one_empty(self):
        result = apply_template(
            "{{ if and .A .B }}yes{{ else }}no{{ end }}",
            {".A": "x", ".B": ""},
        )
        assert result == "no"

    def test_or_one_truthy(self):
        result = apply_template(
            "{{ if or .A .B }}yes{{ else }}no{{ end }}",
            {".A": "", ".B": "y"},
        )
        assert result == "yes"

    def test_eq_equal(self):
        result = apply_template(
            '{{ if eq .Val "test" }}match{{ else }}no{{ end }}',
            {".Val": "test"},
        )
        assert result == "match"

    def test_eq_not_equal(self):
        result = apply_template(
            '{{ if eq .Val "other" }}match{{ else }}no{{ end }}',
            {".Val": "test"},
        )
        assert result == "no"

    def test_ne(self):
        result = apply_template(
            '{{ if ne .Val "test" }}different{{ else }}same{{ end }}',
            {".Val": "other"},
        )
        assert result == "different"


class TestBuiltinFunctions:
    def test_join(self):
        result = apply_template(
            '{{ join .Items "," }}',
            {".Items": ["a", "b", "c"]},
        )
        assert result == "a,b,c"

    def test_re_replace(self):
        result = apply_template(
            '{{ re_replace .Text "world" "python" }}',
            {".Text": "hello world"},
        )
        assert result == "hello python"


class TestRealWorldTemplates:
    """Templates from actual Jackett YAML definitions."""

    def test_1337x_search_path(self):
        # Simplified version of 1337x search path
        tmpl = "{{ if .Keywords }}search/{{ .Keywords }}{{ else }}cat/Movies{{ end }}/1/"
        result = apply_template(tmpl, {".Keywords": "breaking bad"})
        assert result == "search/breaking bad/1/"

    def test_1337x_no_keywords(self):
        tmpl = "{{ if .Keywords }}search/{{ .Keywords }}{{ else }}cat/Movies{{ end }}/1/"
        result = apply_template(tmpl, {".Keywords": ""})
        assert result == "cat/Movies/1/"

    def test_abnormal_categories(self):
        tmpl = "{{ range .Categories }}categoryId={{.}}&{{ end }}"
        result = apply_template(tmpl, {".Categories": ["1", "2"]})
        assert result == "categoryId=1&categoryId=2&"

    def test_abnormal_freeleech(self):
        tmpl = "{{ if .Config.freeleech }}true{{ else }}{{ end }}"
        result = apply_template(tmpl, {".Config.freeleech": "true"})
        assert result == "true"

    def test_result_reference(self):
        tmpl = "{{ if .Result.title_optional }}{{ .Result.title_optional }}{{ else }}{{ .Result.title_default }}{{ end }}"
        result = apply_template(tmpl, {
            ".Result.title_optional": "",
            ".Result.title_default": "Fallback Title",
        })
        assert result == "Fallback Title"

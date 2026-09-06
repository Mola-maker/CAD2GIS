import pytest

from tools.review_reader_migration import census, compare


def changes():
    return {'relocated_source_paths': 0, 'color_encoding_changes': [], 'numeric_changes': []}


def test_migration_compares_rgb_but_never_rewrites_labels_or_geometry():
    result = changes()
    compare({'true_color': '#-3D7FFF60', 'source_file': 'old.dwg', 'point': [0., 2.]},
            {'true_color': '#8000A0', 'source_file': 'new.dwg', 'point': [0., 2.+1e-12]}, '/1', result)
    assert len(result['color_encoding_changes']) == 1
    assert len(result['numeric_changes']) == 1
    for before, after in [({'text': '#-3D7FFF60'}, {'text': '#8000A0'}),
                          ({'point': [0., 1.]}, {'point': [0., 1.001]}),
                          ({'handle': 'AA'}, {'handle': 'BB'}),
                          ({'point': [0., 1.]}, {'point': [0., 1., 0.]})]:
        with pytest.raises(ValueError):
            compare(before, after, '/1', changes())


def test_census_migration_only_equates_color_bits():
    a = {'style_facts': {'ACI:1|TRUECOLOR:#-3D7FFF60|LINEWEIGHT:30': 2}, 'layers': {'cable': 3}}
    b = {'style_facts': {'ACI:1|TRUECOLOR:#C28000A0|LINEWEIGHT:30': 2}, 'layers': {'cable': 3}}
    assert census(a) == census(b)
    b['layers']['cable'] = 4
    assert census(a) != census(b)

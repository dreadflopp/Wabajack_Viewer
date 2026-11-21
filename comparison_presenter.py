"""Project comparison evidence onto the main mod browser without changing authored rows."""
from bisect import bisect_left


FILTERS = ('All mods', 'Differences / unknown', 'Wabbajack only', 'Installed only', 'Matches',
           'Version differs', 'Order differs', 'Enabled state differs', 'Files differ', 'Needs review')


def category(row):
    if not row['package_name']:
        return 'Installed only' if not row['candidates'] else 'Needs review'
    if not row['local_name']:
        return 'Wabbajack only' if not row['candidates'] else 'Needs review'
    if row['status'] == 'Missing folder':
        return 'Different'
    if row['status'] in ('Version unknown', 'Ambiguous match'):
        return 'Needs review'
    if (row['status'] != 'Metadata matches' or row['enabled_differs'] or row['order_differs']
            or row['content']['status'] == 'Files differ'):
        return 'Different'
    if row['content']['status'] == 'Files unverified':
        return 'Needs review'
    return 'Matches'


def difference_label(row):
    group = category(row)
    if group != 'Different':
        return group
    labels = []
    if row['status'] != 'Metadata matches':
        labels.append(row['status'])
    if row['content']['status'] == 'Files differ':
        labels.append('Files differ')
    if row['enabled_differs']:
        labels.append('Enabled differs')
    if row['order_differs']:
        labels.append('Order differs')
    return '; '.join(labels)


def result_label(row):
    result = difference_label(row)
    basis = row.get('match_basis', '')
    return result + (' — ' + basis if basis else '')


def matches_filter(row, selected):
    if selected == 'All mods':
        return True
    if not row:
        return False
    group = category(row)
    return {'Differences / unknown': group != 'Matches', 'Wabbajack only': group == 'Wabbajack only',
            'Installed only': group == 'Installed only', 'Matches': group == 'Matches',
            'Version differs': row['status'] == 'Version differs', 'Order differs': row['order_differs'],
            'Enabled state differs': row['enabled_differs'], 'Files differ': row['content']['status'] == 'Files differ',
            'Needs review': group == 'Needs review'}.get(selected, False)


def display_rows(authored, report):
    by_name = {r['package_name'].casefold(): r for r in report['rows'] if r['package_name']}
    colors = {'Matches': 'green', 'Different': 'yellow', 'Needs review': 'yellow',
              'Wabbajack only': 'red', 'Installed only': 'external_only'}
    result = []
    anchors = []
    for index, original in enumerate(authored):
        row = dict(original)
        comparison = by_name.get(str(row.get('raw_name') or row['name']).casefold())
        row['comparison'] = comparison
        row['compare_status'] = colors[category(comparison)] if comparison else None
        result.append(row)
        if comparison and comparison['local_name']:
            anchors.append((comparison['local_order'], index))
    anchors.sort()
    positions = [a[0] for a in anchors]
    inserts = {}
    for comparison in sorted((r for r in report['rows'] if not r['package_name']), key=lambda r: r['local_order']):
        at = bisect_left(positions, comparison['local_order'])
        # Place personal additions beside their nearest preceding matched mod.
        index = anchors[at - 1][1] + 1 if at else anchors[0][1] if anchors else len(authored)
        name = comparison['local_name']
        row = dict(number='', name=name, raw_name=name, folder_key=name.casefold(), active=False,
                   activation_state='inactive', is_separator=False, author='', version='', size_mb='',
                   archive_hash='', archive_name='', archive=None, folder_meta={}, source_archives=[],
                   folder_files=[], source_file_counts={}, installed_only=True, comparison=comparison,
                   compare_status=colors[category(comparison)])
        inserts.setdefault(index, []).append(row)
    return [row for index in range(len(result) + 1)
            for row in [*inserts.get(index, []), *([result[index]] if index < len(result) else [])]]

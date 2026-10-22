"""Stable collision-free task/constraint pair keys, compatible with ordinary IDs."""
def pair_key(task_id,constraint_id):
    # Only task component needs escaping: its unescaped separator is unique.
    # Percent must be escaped first to distinguish literal '%3A' from ':'.
    return str(task_id).replace('%','%25').replace(':','%3A')+':'+str(constraint_id)


def split_pair_key(value):
    task,separator,constraint=str(value).partition(':')
    if not separator:raise ValueError('Invalid task/constraint pair key')
    return task.replace('%3A',':').replace('%25','%'),constraint

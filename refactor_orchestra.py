import os

replacements = {
    'CassiopeiaManager': 'CassiopeiaManager',
    'CassiopeiaTask': 'CassiopeiaTask',
    'CassiopeiaAgentProtocol': 'CassiopeiaAgentProtocol',
    'CassiopeiaManagerProtocol': 'CassiopeiaManagerProtocol',
    'Cassiopeia Agent': 'Cassiopeia Agent',
    'Cassiopeia': 'Cassiopeia',
    'Cassiopeia': 'Cassiopeia',
    'cassiopeia:': 'cassiopeia:',
    'cassiopeia_': 'cassiopeia_',
    'agent:cassiopeia': 'agent:cassiopeia',
    'provider="cassiopeia"': 'provider="cassiopeia"',
    "provider='cassiopeia'": "provider='cassiopeia'",
    '[CassiopeiaManager]': '[CassiopeiaManager]',
    'CASSIOPEIA_': 'CASSIOPEIA_',
    'cassiopeia': 'cassiopeia',
    '카시오페아': '카시오페아'
}

count = 0
for root, _, files in os.walk('.'):
    if 'venv' in root or '.git' in root or '__pycache__' in root or '.pytest_cache' in root:
        continue
    for file in files:
        if file.endswith('.py') or file.endswith('.yml') or file.endswith('.txt') or file.endswith('.sh') or file.endswith('.md'):
            path = os.path.join(root, file)
            with open(path, 'r', encoding='utf-8') as f:
                try:
                    content = f.read()
                except UnicodeDecodeError:
                    continue
            
            original_content = content
            for old, new in replacements.items():
                content = content.replace(old, new)
                
            if content != original_content:
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(content)
                count += 1
print(f'Total files updated: {count}')

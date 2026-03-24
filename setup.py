"""
Zotero AI Daily Papers - Setup Configuration
"""

from setuptools import setup, find_packages
import os

# Read README for long description
def read_readme():
    readme_path = os.path.join(os.path.dirname(__file__), 'README.md')
    with open(readme_path, 'r', encoding='utf-8') as f:
        return f.read()

def read_requirements():
    requirements_path = os.path.join(os.path.dirname(__file__), 'requirements.txt')
    with open(requirements_path, 'r', encoding='utf-8') as f:
        return [line.strip() for line in f if line.strip() and not line.startswith('#')]

setup(
    name='zotero-ai-daily-papers',
    version='1.0.0',
    author='paperRead Contributors',
    author_email='your-email@example.com',
    description='AI-powered automated academic paper fetching, analysis, and archiving system',
    long_description=read_readme(),
    long_description_content_type='text/markdown',
    url='https://github.com/your-username/paperRead',
    project_urls={
        'Bug Reports': 'https://github.com/your-username/paperRead/issues',
        'Source': 'https://github.com/your-username/paperRead',
        'Documentation': 'https://github.com/your-username/paperRead/blob/main/README.md',
    },
    packages=find_packages(),
    classifiers=[
        'Development Status :: 4 - Beta',
        'Intended Audience :: Science/Research',
        'Topic :: Scientific/Engineering :: Artificial Intelligence',
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
        'Operating System :: OS Independent',
    ],
    python_requires='>=3.8',
    install_requires=read_requirements(),
    entry_points={
        'console_scripts': [
            'paperread=main:main',
            'paperread-index=zotero_indexer:build_knowledge_base',
        ],
    },
    include_package_data=True,
    keywords='arxiv zotero academic papers ai llm automation',
    license='MIT',
)

import setuptools

with open('README.md', 'r', encoding='utf-8') as fh:
    long_description = fh.read()

setuptools.setup(
    name='django-simple-api-proxy',
    fullname='django-simple-api-proxy',
    author='Nat Lee',
    author_email='natlee.work@gmail.com',
    description='Easy to proxy any APIs you want.',
    keywords='django, api, proxy, proxy pass',
    long_description=long_description,
    long_description_content_type='text/markdown',
    url='https://github.com/NatLee/django-simple-api-proxy',
    project_urls={
        'Documentation': 'https://github.com/NatLee/django-simple-api-proxy',
        'Bug Reports': 'https://github.com/NatLee/django-simple-api-proxy/issues',
        'Source Code': 'https://github.com/NatLee/django-simple-api-proxy',
        # 'Funding': '',
        # 'Say Thanks!': '',
    },
    package_dir={'': 'src'},
    packages=setuptools.find_packages(where='src'),
    classifiers=[
        # see https://pypi.org/classifiers/
        'Development Status :: 5 - Production/Stable',

        'Intended Audience :: Developers',
        'Topic :: Internet :: WWW/HTTP',

        'Framework :: Django',
        'Framework :: Django :: 4.0',
        'Framework :: Django :: 4.1',
        'Framework :: Django :: 4.2',
        'Framework :: Django :: 5.0',
        'Framework :: Django :: 5.1',
        'Framework :: Django :: 5.2',
        'Framework :: Django :: 6.0',
        'Framework :: Django :: 6.1',

        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
        'Programming Language :: Python :: 3.12',
        'Programming Language :: Python :: 3.13',
        'Programming Language :: Python :: 3.14',
        'Programming Language :: Python :: 3 :: Only',
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
    ],
    python_requires='>=3.8',
    install_requires=['django>=4.0.0', 'requests', 'loguru', 'djangorestframework'],
    extras_require={
        'dev': ['check-manifest'],
        'test': ['pytest', 'djangorestframework-simplejwt'],
    },
)

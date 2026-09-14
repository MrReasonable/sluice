class JobSluice < Formula
  include Language::Python::Virtualenv

  desc "Engineered, config-driven job-hunting pipeline"
  homepage "https://github.com/MrReasonable/sluice"
  url "https://example.invalid/packages/ab/cd/job_sluice-9.9.0.tar.gz"
  sha256 "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
  license "MIT"

  bottle do
    root_url "https://github.com/exampleowner/homebrew-tap/releases/download/job-sluice-9.9.0-1-1"
    sha256 cellar: :any, arm64_tahoe:   "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    sha256 cellar: :any, arm64_sequoia: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  end

  # No `version "..."` stanza here, deliberately. Homebrew's canonical component order is
  # `url, mirror, version, sha256, license` (`FormulaAudit/ComponentsOrder`, a plain cop that
  # fires even without --strict), which the emitted `url`/`sha256`/`license` above already
  # satisfy in the absence of a `version` line -- but a `version` line, wherever placed, would
  # ALSO be flagged by `resource_auditor.rb` as "redundant with version scanned from URL":
  # `Version.detect` on a PyPI sdist filename returns the identical string, so `brew audit
  # --strict --online` fails either way. Homebrew's own `version` -- this Formula's DSL-level
  # accessor, POPULATED by that same `Version.detect` call against the `url` above, not
  # anything this file passes in -- is what `test do`'s `assert_match version.to_s, ...` below
  # reads, so nothing here needs to emit a version a second time.

  depends_on "cffi"
  depends_on "cryptography"
  depends_on "libyaml"
  depends_on "pango"
  depends_on "pillow"
  depends_on "pydantic"
  depends_on "python@3.14"
  depends_on "rpds-py"
  uses_from_macos "libffi"

  pypi_packages package_name:     "job-sluice[render,google,mcp,completion]",
                # Padded to align with `exclude_packages:` below -- RuboCop's
                # Layout/HashAlignment wants a multi-line hash literal's values in one
                # column, and `brew audit --strict` runs it. Measured, not guessed.
                extra_packages:   %w[typing-extensions],
                exclude_packages: %w[cffi cryptography pillow pydantic rpds-py]

  resource "anyio" do
    url "https://files.pythonhosted.org/packages/a9/d2/f4d173e22df740bc37b1db102b386ba719b66e95b0f0d751f556b387e6d2/anyio-4.15.1.tar.gz"
    sha256 "9f28306018cbd6d329e64a36d58256edff76dd996fe423bc957326e578b82a94"
  end

  resource "argcomplete" do
    url "https://files.pythonhosted.org/packages/87/6f/5a73f04007ca950701765949209f068da628bd11f9c2da287278ce91e0ee/argcomplete-3.7.2.tar.gz"
    sha256 "aad8b69a0b9969edb62db0d1752354c0d50717b10e0cbb00e2a958381b9fc6b9"
  end

  resource "attrs" do
    url "https://files.pythonhosted.org/packages/9a/8e/82a0fe20a541c03148528be8cac2408564a6c9a0cc7e9171802bc1d26985/attrs-26.1.0.tar.gz"
    sha256 "d03ceb89cb322a8fd706d4fb91940737b6642aa36998fe130a9bc96c985eff32"
  end

  resource "brotli" do
    url "https://files.pythonhosted.org/packages/f7/16/c92ca344d646e71a43b8bb353f0a6490d7f6e06210f8554c8f874e454285/brotli-1.2.0.tar.gz"
    sha256 "e310f77e41941c13340a95976fe66a8a95b01e783d430eeaf7a2f87e0a57dd0a"
  end

  resource "certifi" do
    url "https://files.pythonhosted.org/packages/a3/c2/24167ea9858356b47a87a50d39908bfdb72ceeefe0041586e704e5376b3a/certifi-2026.7.22.tar.gz"
    sha256 "741e2c3b351ddf169a738da9f2c048608ff7f2c5cc02f1ebc6b118bb090d5d55"
  end

  resource "charset-normalizer" do
    url "https://files.pythonhosted.org/packages/e5/3f/143b048436775b0f76ac3eec145c019e8173ccc2885c8f20319b996d5e83/charset_normalizer-3.5.1.tar.gz"
    sha256 "6117b84ea48435e5356dc737f5121485c30920ba43375fa7b434fd753df0eac3"
  end

  resource "click" do
    url "https://files.pythonhosted.org/packages/c7/0e/7fa0ef50764b67090eca4114772a2abf8b6148198475e54c660b97caeee6/click-8.5.0.tar.gz"
    sha256 "ba0d2089de75ea0310e2dde03160e6ca10009947fb95a182f9b54021bb272e34"
  end

  resource "cssselect2" do
    url "https://files.pythonhosted.org/packages/06/00/2456b6b664c7a770989cbe3c352aac4eb962c938486f03a2e1255ae963c6/cssselect2-0.10.1.tar.gz"
    sha256 "83b0d820ef589dabaf693289b647c2f5b410f76d285f56deba911ffa75a7b9d1"
  end

  resource "fonttools" do
    url "https://files.pythonhosted.org/packages/77/51/d63c7e52163ac14393a35bd14bd7c0da95f8f74be5d7cc988092f9965129/fonttools-4.65.0.tar.gz"
    sha256 "762ba5431358d0dbd4a01982484a1d494fb267e91f974cdcf20b80eab8560f6f"
  end

  resource "google-api-core" do
    url "https://files.pythonhosted.org/packages/bf/d8/88c2f0e6b0dd46a7796cca64fad99c7adba2417916f5393e82b9b7d2548e/google_api_core-2.36.0.tar.gz"
    sha256 "32779307b52e64c9a9592a3621de6281676ecaeea299fe8524e4637ab7ac2531"
  end

  resource "google-api-python-client" do
    url "https://files.pythonhosted.org/packages/fd/e5/12024a0ae2fd39a54ff47a3868c47344ffff4ff1cd5edf4dff523c1a9fc1/google_api_python_client-2.200.0.tar.gz"
    sha256 "82aa18b851328ea04867fd51c5a0c8da2e1b86ec45ce08487e902e7726d4ee50"
  end

  resource "google-auth" do
    url "https://files.pythonhosted.org/packages/ac/ca/f398a483ce5aad18ca2f735646e45ccee2439bd94a41a4ad0cfa646bd495/google_auth-2.58.0.tar.gz"
    sha256 "55e30cf15e737de92c5323d78cda8a83fcd57e7ffbaf900c4600039fd60a80fd"
  end

  resource "google-auth-httplib2" do
    url "https://files.pythonhosted.org/packages/d4/74/0c8177b73734dfbd89420c162ac8754257fa0f9007fb49569493d83a17db/google_auth_httplib2-0.4.2.tar.gz"
    sha256 "916225a6367e613c9af44d83f41688a599d3f687777846b8b91bec65085ed1f1"
  end

  resource "google-auth-oauthlib" do
    url "https://files.pythonhosted.org/packages/dd/fb/e8def92f788410d96d1aff0cadfadb3f044bbffbe3d2560a1ad8fa0d9466/google_auth_oauthlib-1.4.1.tar.gz"
    sha256 "1a83f5f2a8421dedadaa3caf25b3a710dddf85a33a63144be41c2fc79174b106"
  end

  resource "googleapis-common-protos" do
    url "https://files.pythonhosted.org/packages/8a/c5/4353a188e2c335aee33269e8b654af228278cca8e5f0b4b5f11e5d0e9adb/googleapis_common_protos-1.75.3.tar.gz"
    sha256 "57c435ac2c68b108999b6db075d9053e4d7a936ba57b4a3d45667b1346f1738a"
  end

  resource "h11" do
    url "https://files.pythonhosted.org/packages/01/ee/02a2c011bdab74c6fb3c75474d40b3052059d95df7e73351460c8588d963/h11-0.16.0.tar.gz"
    sha256 "4e35b956cf45792e4caa5885e69fba00bdbc6ffafbfa020300e549b208ee5ff1"
  end

  resource "httpcore2" do
    url "https://files.pythonhosted.org/packages/be/ad/f4f0e57345f1870f3e8cb624e058d7eca6e5a27d33bcc3311d9b618734cd/httpcore2-2.12.0.tar.gz"
    sha256 "9293522bba0aa7c4c8e9e3f040c16575bd8868e155a77fa30c7a9085a5eae648"
  end

  resource "httplib2" do
    url "https://files.pythonhosted.org/packages/84/f5/ccf58de92d61e3ad921119668f54ed36ca1d0cf5dcc5c1657dfb164fd78b/httplib2-0.32.0.tar.gz"
    sha256 "48a0ef30a42db65d8f3399045e1d09ab0ba66e3b9efc360d07f80ea55d286025"
  end

  resource "httpx2" do
    url "https://files.pythonhosted.org/packages/7f/f8/579a8b51e42e38ee32647df9f08aa25643ae788e275cc625b199829c4671/httpx2-2.12.0.tar.gz"
    sha256 "7631fe9887a8a2275f4a2540e053aa670fcc50742864a9ae7c66e609fdcf12cf"
  end

  resource "idna" do
    url "https://files.pythonhosted.org/packages/5f/f7/abb373e5757eaec4b922b92f97ec8d6d7e057cf06778247604fbc4e7c3f3/idna-3.19.tar.gz"
    sha256 "5e0811a4383b21dc5838069f801c4fb62113b7447663d2530d2bd6e77b49bf15"
  end

  resource "jinja2" do
    url "https://files.pythonhosted.org/packages/df/bf/f7da0350254c0ed7c72f3e33cef02e048281fec7ecec5f032d4aac52226b/jinja2-3.1.6.tar.gz"
    sha256 "0137fb05990d35f1275a587e9aee6d56da821fc83491a0fb838183be43f66d6d"
  end

  resource "jsonschema" do
    url "https://files.pythonhosted.org/packages/b3/fc/e067678238fa451312d4c62bf6e6cf5ec56375422aee02f9cb5f909b3047/jsonschema-4.26.0.tar.gz"
    sha256 "0c26707e2efad8aa1bfc5b7ce170f3fccc2e4918ff85989ba9ffa9facb2be326"
  end

  resource "jsonschema-specifications" do
    url "https://files.pythonhosted.org/packages/19/74/a633ee74eb36c44aa6d1095e7cc5569bebf04342ee146178e2d36600708b/jsonschema_specifications-2025.9.1.tar.gz"
    sha256 "b540987f239e745613c7a9176f3edb72b832a4ac465cf02712288397832b5e8d"
  end

  resource "markupsafe" do
    url "https://files.pythonhosted.org/packages/7e/99/7690b6d4034fffd95959cbe0c02de8deb3098cc577c67bb6a24fe5d7caa7/markupsafe-3.0.3.tar.gz"
    sha256 "722695808f4b6457b320fdc131280796bdceb04ab50fe1795cd540799ebe1698"
  end

  resource "mcp" do
    url "https://files.pythonhosted.org/packages/76/31/ac54fb0fdd5b37de704486e288bba4fbbb463f24cfcfedbede407b854513/mcp-2.2.0.tar.gz"
    sha256 "2dc37ecb1974becdcebdbf7561e7c15a07dbbf20ba21ba16c3593b3038b3afbd"
  end

  resource "mcp-types" do
    url "https://files.pythonhosted.org/packages/ae/91/762d7755d971aff8a28d75f7961656148edf27875c8026e6385aaab08ae7/mcp_types-2.2.0.tar.gz"
    sha256 "d3ed53703ddd10d9c6399f29d322bb66f3f67ab41348ac8556ba23e07fedefad"
  end

  resource "oauthlib" do
    url "https://files.pythonhosted.org/packages/0b/5f/19930f824ffeb0ad4372da4812c50edbd1434f678c90c2733e1188edfc63/oauthlib-3.3.1.tar.gz"
    sha256 "0f0f8aa759826a193cf66c12ea1af1637f87b9b4622d46e866952bb022e538c9"
  end

  resource "opentelemetry-api" do
    url "https://files.pythonhosted.org/packages/ee/8b/aa9e2d8b8dfa7c946f7dec5d1f8f6ba8eca062f43509a06bdb5ce93d26c0/opentelemetry_api-1.44.0.tar.gz"
    sha256 "67647e5e9566edcf421166fdf022b3537f818635daa852b289e34604dc6fb33a"
  end

  resource "proto-plus" do
    url "https://files.pythonhosted.org/packages/40/a6/4fbadcc2044034449b3f8f0ce82dcf3005d53f37c136642103fd4836a31c/proto_plus-1.28.4.tar.gz"
    sha256 "5ff7ecad828e032a491fcb86947801768e32237f99dd049b649965b892ae9a63"
  end

  resource "protobuf" do
    url "https://files.pythonhosted.org/packages/86/73/f66c748df06e7fe24e658eddd600d19c4b40bad836c97ce2d0ad9851fb6b/protobuf-7.36.1.tar.gz"
    sha256 "d0f6470f0ce2b84e3feaea2d4b816378b37ba4d4aa08a274305373de93e2d524"
  end

  resource "pyasn1" do
    url "https://files.pythonhosted.org/packages/a4/9a/23310166d960def5897e91fe20e5b724601b02a22e84ba1f94232c0b7f67/pyasn1-0.6.4.tar.gz"
    sha256 "9c447d8431c947fe4c8febc4ed9e760bc29011a5b01e5c74b67025bd9fb8ce81"
  end

  resource "pyasn1-modules" do
    url "https://files.pythonhosted.org/packages/e9/e6/78ebbb10a8c8e4b61a59249394a4a594c1a7af95593dc933a349c8d00964/pyasn1_modules-0.4.2.tar.gz"
    sha256 "677091de870a80aae844b1ca6134f54652fa2c8c5a52aa396440ac3106e941e6"
  end

  resource "pydyf" do
    url "https://files.pythonhosted.org/packages/36/ee/fb410c5c854b6a081a49077912a9765aeffd8e07cbb0663cfda310b01fb4/pydyf-0.12.1.tar.gz"
    sha256 "fbd7e759541ac725c29c506612003de393249b94310ea78ae44cb1d04b220095"
  end

  resource "pyjwt" do
    url "https://files.pythonhosted.org/packages/af/c3/8a3b59c25070cc61dc517fbdfa5dc0904670c96f605cc69759dc09166b99/pyjwt-2.14.0.tar.gz"
    sha256 "77283c83fb56ecf566a886c757a714bc83668e38156de2cce8263302f42e0b86"
  end

  resource "pyparsing" do
    url "https://files.pythonhosted.org/packages/f3/91/9c6ee907786a473bf81c5f53cf703ba0957b23ab84c264080fb5a450416f/pyparsing-3.3.2.tar.gz"
    sha256 "c777f4d763f140633dcb6d8a3eda953bf7a214dc4eff598413c070bcdc117cbc"
  end

  resource "pyphen" do
    url "https://files.pythonhosted.org/packages/94/47/8430452269cd28863d73b903d07d329d058cf762527ff211b3864ba61fc7/pyphen-0.18.1.tar.gz"
    sha256 "dbae6fbbe4f01cb206108b43573d857c67107be9d0e38eb1b08d6fa2210634a7"
  end

  resource "python-multipart" do
    url "https://files.pythonhosted.org/packages/5b/42/55c32bb9b12693c092ad250a0e82edb5b31ddeda6eb772de5f308b3804ad/python_multipart-0.0.32.tar.gz"
    sha256 "be54b7f3fa167bb83e4fcd936b887b708f4e57fe75911c02aebf53efaf8d938e"
  end

  resource "pyyaml" do
    url "https://files.pythonhosted.org/packages/05/8e/961c0007c59b8dd7729d542c61a4d537767a59645b82a0b521206e1e25c2/pyyaml-6.0.3.tar.gz"
    sha256 "d76623373421df22fb4cf8817020cbb7ef15c725b9d5e45f17e189bfc384190f"
  end

  resource "referencing" do
    url "https://files.pythonhosted.org/packages/22/f5/df4e9027acead3ecc63e50fe1e36aca1523e1719559c499951bb4b53188f/referencing-0.37.0.tar.gz"
    sha256 "44aefc3142c5b842538163acb373e24cce6632bd54bdb01b21ad5863489f50d8"
  end

  resource "requests" do
    url "https://files.pythonhosted.org/packages/ac/c3/e2a2b89f2d3e2179abd6d00ebd70bff6273f37fb3e0cc209f48b39d00cbf/requests-2.34.2.tar.gz"
    sha256 "f288924cae4e29463698d6d60bc6a4da69c89185ad1e0bcc4104f584e960b9ed"
  end

  resource "requests-oauthlib" do
    url "https://files.pythonhosted.org/packages/42/f2/05f29bc3913aea15eb670be136045bf5c5bbf4b99ecb839da9b422bb2c85/requests-oauthlib-2.0.0.tar.gz"
    sha256 "b3dffaebd884d8cd778494369603a9e7b58d29111bf6b41bdc2dcd87203af4e9"
  end

  resource "sse-starlette" do
    url "https://files.pythonhosted.org/packages/2b/54/6767bb789b2f2fed6e0f953df949cd39dc263a384c1b65a95232598621d6/sse_starlette-3.4.11.tar.gz"
    sha256 "1bae716c02f3e6f294be41ff333220692dae7c3cbab077c900f159676719dade"
  end

  resource "starlette" do
    url "https://files.pythonhosted.org/packages/b5/b4/205b0d5241d934e8add0c38aa924c4f9fb7330834ff11e5444db964ec3f9/starlette-1.6.0.tar.gz"
    sha256 "d4e3ac5e546444960c710297a3c9fc3f7ebae1b7e963f3d36173b49da535be9b"
  end

  resource "tinycss2" do
    url "https://files.pythonhosted.org/packages/a3/ae/2ca4913e5c0f09781d75482874c3a95db9105462a92ddd303c7d285d3df2/tinycss2-1.5.1.tar.gz"
    sha256 "d339d2b616ba90ccce58da8495a78f46e55d4d25f9fd71dfd526f07e7d53f957"
  end

  resource "tinyhtml5" do
    url "https://files.pythonhosted.org/packages/b1/1f/cfe2f6b30557c92b3f31d41707e09cef5c1efbd87392bc6c0430c46b0e4d/tinyhtml5-2.1.0.tar.gz"
    sha256 "60a50ec3d938a37e491efa01af895853060943dcebb5627de5b10d188b338a67"
  end

  resource "truststore" do
    url "https://files.pythonhosted.org/packages/53/a3/1585216310e344e8102c22482f6060c7a6ea0322b63e026372e6dcefcfd6/truststore-0.10.4.tar.gz"
    sha256 "9d91bd436463ad5e4ee4aba766628dd6cd7010cf3e2461756b3303710eebc301"
  end

  resource "typing-extensions" do
    url "https://files.pythonhosted.org/packages/f6/cc/6253133b5bb138fc3306cebfbda2c520f545d36b5be2c7255cc528bb45d6/typing_extensions-4.16.0.tar.gz"
    sha256 "dc983d19a509c94dba722ee6abd33940f7c05a89e243c47e907eb4db6f1a43e5"
  end

  resource "tzdata" do
    url "https://files.pythonhosted.org/packages/e4/31/3d74fa778a63b98b7374323befcc0be5ab3bd94afd4096a0124e7379152c/tzdata-2026.4.tar.gz"
    sha256 "f1b8bd365d8d210c55353f4d7f8d6d8561c0ba50d704b700d195a9424bba0d79"
  end

  resource "uritemplate" do
    url "https://files.pythonhosted.org/packages/98/60/f174043244c5306c9988380d2cb10009f91563fc4b31293d27e17201af56/uritemplate-4.2.0.tar.gz"
    sha256 "480c2ed180878955863323eea31b0ede668795de182617fef9c6ca09e6ec9d0e"
  end

  resource "urllib3" do
    url "https://files.pythonhosted.org/packages/53/0c/06f8b233b8fd13b9e5ee11424ef85419ba0d8ba0b3138bf360be2ff56953/urllib3-2.7.0.tar.gz"
    sha256 "231e0ec3b63ceb14667c67be60f2f2c40a518cb38b03af60abc813da26505f4c"
  end

  resource "uvicorn" do
    url "https://files.pythonhosted.org/packages/f2/0f/3f86e61397dd33bf2ccf28188c40db6a740658aeebbbf6e7dbc101a1f487/uvicorn-0.52.4.tar.gz"
    sha256 "73acfee47a0b133c5de13d219492d62d8a31e935f4fe6e41a232451a15379f86"
  end

  resource "weasyprint" do
    url "https://files.pythonhosted.org/packages/59/53/dcc3885c2f7a47faa45f6b8b801412f5f9e055173a52801ef01c09943c5a/weasyprint-69.0.tar.gz"
    sha256 "a7a32f39ca16bd82ef11de99c92ea4b5f14951c9033af035e451ce4f4ee0a88c"
  end

  resource "webencodings" do
    url "https://files.pythonhosted.org/packages/d5/a0/8fd707bcb776a7be556bad06a2ea5fb9bd519df78ef8e26f70ccf0f38bff/webencodings-0.6.1.tar.gz"
    sha256 "565f9ad031c702dae404e27a099e3e09186a3ab1b9520f06d215502b651fd910"
  end

  resource "zopfli" do
    url "https://files.pythonhosted.org/packages/74/21/3b6af43a663b22b00e738bb0642931a2579e15da6852613d56c6aa535d28/zopfli-0.4.3.tar.gz"
    sha256 "d3a50f91a13cea9bafe025de8fd87a005eb26de02a4f0c193127ddbf23ac8ebe"
  end

  def install
    virtualenv_install_with_resources
  end

  test do
    # The ambient environment is NOT clean: any SLUICE_*/CAMOFOX_* variable, or one of this
    # project's other path-shaped env vars, would point a local `brew test` at the
    # maintainer's real vault, config, dedup state, health/audit state, dossier cache, or a
    # real camofox server -- SLUICE_TELEGRAM_TOKEN and SLUICE_TELEGRAM_CHAT in particular are
    # a CREDENTIAL pair sluice/core/log.py reads ahead of config and POSTS with. Swept by NAME
    # PATTERN rather than hand-listed: an earlier version of this block named only
    # SLUICE_CONFIG and VAULT_DIR while this very comment already stated the general
    # principle -- two reviewers independently caught the gap, and CLAUDE.md's "hand-listed
    # names lose" lesson applies here exactly as it does to a Python AST sweep.
    # `to_h` snapshots before iterating. Measured on this Ruby, deleting from ENV during a
    # bare `each_key` is fine -- but depending on a collection's mutation-during-iteration
    # semantics is a hazard worth not taking, and the snapshot costs one allocation. `each_key`
    # rather than `keys.each` because `brew audit --strict` runs Style/HashEachMethods.
    ENV.to_h.each_key do |k|
      ENV.delete(k) if k.match?(/\A(SLUICE|CAMOFOX)_/)
    end
    # Explicitly-named path variables outside that prefix shape -- never hand-guessed, and
    # NOT enumerated from sluice/core/paths.py, which an earlier version of this comment
    # named: that module DEFINES `resolve` and names no variable of its own. The names come
    # from the `resolve(env_var="...")`/`_resolve_path(env_var="...")` CALL SITES and the
    # direct `os.environ.get("...")` reads, which live in other modules under sluice/.
    # Deliberately not listed here by file: tests/test_homebrew_formula.py's
    # `test_the_test_block_sandboxes_every_env_var_sluice_reads` re-derives the whole set by
    # AST-walking sluice/ and fails if a name it finds is neither swept by the pattern above,
    # nor listed below, nor named in that test's own short allow-list of variables that need no
    # sandboxing at all. So this list cannot silently go stale -- and a file list beside it
    # would be one more thing that could.
    %w[VAULT_DIR SEEN_DB TRIAGE_AUDIT DOSSIER_DIR].each do |k|
      ENV.delete(k)
    end
    ENV["HOME"] = testpath
    # All three XDG rungs. sluice/core/paths.py's `resolve()` falls through to the matching
    # rung the instant the explicitly-named var above is deleted: SEEN_DB/SLUICE_HEALTH/
    # TRIAGE_AUDIT/SLUICE_DISABLED to XDG_STATE_HOME, SLUICE_CONFIG to XDG_CONFIG_HOME, and
    # DOSSIER_DIR to XDG_CACHE_HOME -- leaving any one of these three unset here would let that
    # rung fall through to the maintainer's REAL XDG directory instead of this sandbox.
    ENV["XDG_CONFIG_HOME"] = testpath/"config"
    ENV["XDG_STATE_HOME"] = testpath/"state"
    ENV["XDG_CACHE_HOME"] = testpath/"cache"

    assert_match version.to_s, shell_output("#{bin}/job-sluice --version")

    # `doctor --offline` exits 0 on a clean, unconfigured machine, which is exactly what this
    # one is. #243's contract, stated in sluice/core/doctor.py::DoctorReport.exit_code: a
    # component the user has not SUPPLIED yet is SETUP and never reaches the exit code, so no
    # vault directory, no `claude` CLI and no `render` extra are all still 0. Non-zero means
    # something they DID configure is broken.
    #
    # THIS LITERAL WAS `1` FOR TWO RELEASES, and the cost was the whole channel. 2.7.0's
    # `feat(doctor): a verdict by default, and exit 0 on a clean install` inverted the
    # contract; this number did not move; `brew test` then failed the `homebrew` job on 2.7.0
    # and 2.8.0 while every other channel shipped from those same runs, so the public tap went
    # on serving the last version whose job passed. The justification lived only in a comment
    # here that ended "Measured." -- true when written, and nothing could tell when it stopped
    # being true, which is CLAUDE.md's "a comment that states a mechanism needs a row that
    # falsifies it" applied to a release channel.
    # tests/test_homebrew_formula.py::test_the_formula_expects_the_real_clean_install_exit_code
    # is that row: it RUNS `doctor --offline` and compares, so the formula's expectation and
    # the program's behaviour can no longer drift apart in silence.
    #
    # The code is NOT passed explicitly, and that is forced rather than chosen. `shell_output`
    # defaults to 0 and `brew audit`'s RuboCop pass rejects restating it:
    # `FormulaAudit/Test: Passing 0 to shell_output is redundant`. The audit gates the release
    # job, so an explicit `, 0` fails the channel just as surely as the wrong number did --
    # which is how it shipped: 2.9.1 carried `, 0` on the reasoning that a claim this channel
    # had already been broken by should be written down, and that reasoning was never run
    # against the audit. Asserting a mechanism instead of executing it, in the fix for exactly
    # that. `brew style --formula <tap>/<name>` reproduces it locally in seconds.
    #
    # The assertion is unchanged in force: an omitted code still means `brew test` fails unless
    # the command exits 0. Only the spelling moved.
    #
    # This is the only place a release RUNS the shipped binary on a fresh machine and holds it
    # to a status. ci.yml's container smoke deliberately asserts the status in neither
    # direction -- it checks the report is positively present instead -- so it could not have
    # caught this, and `release-please.yml` runs no doctor at all.
    #
    # `--verbose` IS LOAD-BEARING, not extra detail. #243 made `doctor` print a VERDICT by
    # default and demoted the row table to `--verbose`, and the default view lists only rows
    # that still need action -- so a renderer that is `ok`, which is exactly what this formula
    # exists to prove, appears NOWHERE in it. The row assertion below can only ever match the
    # verbose view. Measured: the row shape occurs once under `--verbose` and zero times
    # without it. This cost the channel a third failed release (2.9.2), after the same #243
    # change had already cost it two through the exit code above -- one upstream change, two
    # separate assertions in this block, and fixing the first without auditing the second is
    # what let it repeat.
    report = shell_output("#{bin}/job-sluice doctor --offline --verbose")
    assert_match "job-sluice doctor", report

    # THE PAYOFF, POSITIVE rather than a refutation of "dead": core/app.py's
    # `if cv_cfg is not None:` drops the renderer row ENTIRELY on any load_cv_config error,
    # with exit 1 and the banner intact -- so refuting "dead" passes when the row is merely
    # ABSENT. A negative guard that finds nothing is indistinguishable from success.
    # Row format is `f"{component:12} {subject:32} {state:9} ..."` (cli.py::_print_doctor).
    assert_match(/renderer\s+cv\.renderer\s+ok/, report)

    # ...and independently of sluice's own output format, so a change to doctor's printing
    # cannot silently retire the check above.
    system libexec/"bin/python", "-c",
           "import weasyprint; weasyprint.HTML(string='<p>x</p>').write_pdf('t.pdf')"
    assert_path_exists testpath/"t.pdf"

    # The WeasyPrint probe above proves only the `render` extra. `exclude_packages` above
    # relies on the BREWED interpreter's own site-packages to supply pydantic/rpds-py/cffi --
    # and `mcp` in particular carries a hard pydantic version floor -- so a skew between what
    # this formula ships and what a brewed interpreter's homebrew-core dependencies actually
    # provide would surface as a user-facing ImportError on `mcp`/`google`/`completion` with
    # this job still green. Import each of the other three extras' top-level module or
    # modules the same way the render extra is proven above, against the SAME installed
    # libexec interpreter.
    system libexec/"bin/python", "-c", "import mcp, googleapiclient, google_auth_oauthlib, argcomplete"
  end
end

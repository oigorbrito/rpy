from app.validation import validar

PARTIES = [{"name": "Maria da Silva"}, {"name": "João de Souza"}]
CODE = "0000000-00.0000.0.00.0000"


def test_rejects_clean_cpf_or_cnpj() -> None:
    result = validar(text="CPF 12345678901", code=CODE, parties=PARTIES)
    assert result.passed is False
    assert any("CPF/CNPJ" in error for error in result.errors)


def test_rejects_wrong_cnj() -> None:
    result = validar(
        text="Processo 1111111-11.1111.1.11.1111",
        code=CODE,
        parties=PARTIES,
    )
    assert any("CNJ mismatch" in error for error in result.errors)


def test_rejects_unknown_party() -> None:
    result = validar(
        text='<Party name="Pessoa Inventada" />',
        code=CODE,
        parties=PARTIES,
    )
    assert any("hallucinated parties" in error for error in result.errors)


def test_rejects_unknown_party_in_free_prose_after_role() -> None:
    result = validar(
        text="O autor Pessoa Inventada ajuizou a demanda.",
        code=CODE,
        parties=PARTIES,
    )
    assert any("pessoa inventada" in error for error in result.errors)


def test_rejects_unknown_party_with_copula() -> None:
    result = validar(
        text="A requerida é Empresa Fantasma Ltda.",
        code=CODE,
        parties=PARTIES,
    )
    assert any("empresa fantasma ltda" in error for error in result.errors)


def test_rejects_unknown_party_when_role_follows_name() -> None:
    result = validar(
        text="Pessoa Inventada, na qualidade de autora, apresentou réplica.",
        code=CODE,
        parties=PARTIES,
    )
    assert any("pessoa inventada" in error for error in result.errors)


def test_accepts_known_party_in_free_prose() -> None:
    result = validar(
        text="A autora Maria da Silva apresentou manifestação.",
        code=CODE,
        parties=PARTIES,
    )
    assert result.passed is True


def test_party_matching_tolerates_case_and_diacritic_variation() -> None:
    result = validar(
        text="O réu Joao de Souza apresentou defesa.",
        code=CODE,
        parties=PARTIES,
    )
    assert result.passed is True


def test_does_not_treat_other_named_people_as_parties() -> None:
    result = validar(
        text="O advogado Carlos Pereira falou com a testemunha Ana Ferreira.",
        code=CODE,
        parties=PARTIES,
    )
    assert result.passed is True


def test_rejects_prognostic_language() -> None:
    result = validar(
        text="A parte tende a ganhar a demanda.",
        code=CODE,
        parties=PARTIES,
    )
    assert any("prognostic" in error for error in result.errors)


def test_rejects_invalid_jsx_class_and_balance() -> None:
    result = validar(
        text='<ProcessHeader class="x">conteúdo',
        code=CODE,
        parties=PARTIES,
    )
    assert result.passed is False
    assert any("className" in error for error in result.errors)
    assert any("unclosed JSX" in error for error in result.errors)


def test_accepts_valid_minimal_summary() -> None:
    result = validar(
        text=(
            "# Resumo\n"
            '<ProcessHeader className="x">Processo 0000000-00.0000.0.00.0000</ProcessHeader>\n'
            '<Party name="Maria da Silva" />'
        ),
        code=CODE,
        parties=PARTIES,
    )
    assert result.passed is True

# Refinamento da Central de Alertas — 06/11/2026

## Entrega

- Layout restrito a `/reminders`, com a largura padrão da aplicação, estado vazio compacto, cartões sem altura mínima e ações que se adaptam ao celular.
- Formulário inicialmente oculto em `dialog` nativo, centralizado, com título, data/horário de São Paulo, prioridade e observações. Abertura por **+ Novo lembrete**, edição preenchida, fechamento por X, Esc ou Cancelar; cancelar uma edição e criar outro lembrete limpa também o ID.
- Salvar usa POST com CSRF e redirecionamento para uma consulta atualizada. O modal fecha somente após sucesso; erros retornam o formulário preenchido e mensagens por campo dentro do modal. As operações não exigem reiniciar a aplicação.
- Estilos e comportamento adicionais são carregados somente nesta página, com URLs versionadas para atualizar os arquivos em cache. Outras páginas e os arquivos compartilhados permanecem inalterados.
- Autenticação, propriedade individual dos lembretes, permissões e CSRF preservados. Sem mudanças de esquema, configuração PostgreSQL ou novas migrações.

## Investigação dos zero alertas

Foram confirmados e corrigidos dois problemas: o contador era calculado depois do filtro da aba, anunciando zero mesmo com pendências em outras categorias; e a antecedência **Na hora** tinha uma janela de duração zero, exigindo coincidência até dos microssegundos. O contador agora representa os alertas ativos elegíveis em todas as categorias, exceto adiados, e **Na hora** vale pelo minuto do início. As outras janelas dos compromissos foram mantidas.

Também foram corrigidos o estado vencido do lembrete manual no próprio dia (agora considera horário), a data original de tarefas concluídas e a exibição/edição de manuais para pessoas que perderam acesso à agenda. Um lembrete salvo com prazo além de sete dias recebe uma mensagem explicando a data em que passará a aparecer na Central.

Zero pode continuar sendo correto: compromissos precisam estar pendentes, ter lembrete habilitado e estar na janela configurada; tarefas precisam ter prazo; tarefas, contas não pagas e manuais próprios devem estar vencidos ou dentro dos próximos sete dias; adiamentos futuros ocultam os itens. Permissões restringem as fontes consultadas. A Central não passou a listar todos os compromissos. A conversão UTC/São Paulo dos lembretes foi verificada nos testes.

**Limite da investigação:** não há `DATABASE_URL`, credenciais ou conexão Render nesta sessão. Não foram consultados nem alterados os dados de produção, e não se pode confirmar sua contagem real. O teste PostgreSQL opcional foi ignorado por ausência de `TEST_POSTGRES_DATABASE_URL` para um banco descartável. Para confirmar o zero de produção, um operador deve conferir os prazos, janelas, usuário e adiamentos com os registros reais. Uma auditoria estritamente de leitura deve usar SELECT em transação READ ONLY: `FinanceService.list_bills()` pode materializar ocorrências recorrentes, comportamento anterior a esta mudança.

## Validação executada

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m tests.smoke_reminders_web
git diff --check
```

- Suíte: 142 testes, 141 aprovados e 1 ignorado (PostgreSQL descartável ausente). Inclui criação, edição, leitura, conclusão, adiamento/retorno após expiração, exclusão, persistência, rollback em erro, validações, janelas, fuso, CSRF e isolamento por usuário/permissões.
- Navegador real: Microsoft Edge via Playwright, banco SQLite temporário, sem dados existentes. Validou modal fechado inicialmente, abertura, foco, X/Esc/Cancelar, edição cancelada seguida de criação, validação nativa e do servidor, CRUD, atualização da lista, confirmação de exclusão cancelada/aceita e estados vazios.
- Responsividade em 1440, 1024, 390 e 320 pixels, sem overflow da página/modal; títulos longos e sem espaços também verificados. Capturas temporárias de desktop e celular foram inspecionadas, sem incluir imagens ou dados de testes no repositório.

## Publicação no Render

Publicar o commit desta entrega no serviço conectado ao branch `main`, usando **Deploy latest commit** se o deploy automático não estiver habilitado. Manter build, start, `DATABASE_URL`, `SECRET_KEY` e demais configurações existentes. Esta entrega não adiciona migrações; o comando pre-deploy já configurado pode permanecer.

Depois da publicação, validar por HTTPS o login, os compromissos/lembretes existentes, o painel e o modal. Conferir os zero alertas com os dados reais e o usuário correto. Push no GitHub não certifica que o deploy do Render terminou; o estado da publicação deve ser conferido no serviço.

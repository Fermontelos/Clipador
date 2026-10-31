/** Um erro da API, com o status HTTP (0 = sem resposta). */
export class ErroDaApi extends Error {
  constructor(
    public status: number,
    mensagem: string,
  ) {
    super(mensagem);
  }
}

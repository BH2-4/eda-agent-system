module tiny_fsm(
    input clk,
    input rst,
    input in,
    output reg out
);
    parameter S0 = 1'b0;
    parameter S1 = 1'b1;

    reg state, next;

    always @(posedge clk) begin
        if (rst)
            state <= S0;   // FIXED: reset to S0
        else
            state <= next;
    end

    always @(*) begin
        case (state)
            S0: next = in ? S1 : S0;
            S1: next = in ? S0 : S1;
            default: next = S0;
        endcase
    end

    always @(*) begin
        out = (state == S1);
    end
endmodule